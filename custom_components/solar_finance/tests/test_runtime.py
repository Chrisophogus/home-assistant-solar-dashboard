"""Offline collector and options-flow smoke tests (not live HA load tests)."""

import ast
import asyncio
from datetime import timedelta, timezone
import importlib.util
import sys
import types
import unittest

from test_engine import ROOT, Frozen, c, e


class CollectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree=ast.parse((ROOT/"__init__.py").read_text())
        runtime=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="Runtime")
        method=next(n for n in runtime.body if isinstance(n,ast.FunctionDef) and n.name=="sample")
        method.decorator_list=[]
        namespace={"datetime":Frozen,"timezone":timezone,"ENTITIES":c.ENTITIES,"dec":e.dec,"iso":e.iso}
        exec(compile(ast.Module(body=[method],type_ignores=[]),"actual Runtime.sample","exec"),namespace)
        cls.sample=staticmethod(namespace["sample"])

    def runtime(self):
        states={}
        for key,entity in c.ENTITIES.items():
            states[entity]=types.SimpleNamespace(state="70" if key=="soc" else "Maximum Self Consumption" if key=="mode" else "1.5",attributes={"unit_of_measurement":"%" if key=="soc" else "MWh"},last_reported=Frozen.instant-timedelta(seconds=5))
        return types.SimpleNamespace(sources=c.ENTITIES,session_id="TEST session",hass=types.SimpleNamespace(states=states)),states

    def test_real_collector_units_and_provenance(self):
        runtime,states=self.runtime()
        states[c.ENTITIES["pv"]].attributes["unit_of_measurement"]="Wh"
        sample=self.sample(runtime)
        self.assertEqual(sample["errors"],[])
        self.assertEqual(e.dec(sample["energy"]["import"]),1500)
        self.assertEqual(e.dec(sample["energy"]["pv"]),e.dec(".0015"))
        self.assertEqual(sample["collection_session"],"TEST session")
        self.assertEqual(len(sample["source_timestamps"]),8)

    def test_stale_mode_is_not_attributed_as_current_operation(self):
        runtime,states=self.runtime()
        states[c.ENTITIES["mode"]].last_reported-=timedelta(hours=1)
        sample=self.sample(runtime)
        self.assertIn("stale",sample["mode"])
        self.assertEqual(sample["errors"],[])

    def test_stale_power_counter_and_skew_and_invalid_unit_block(self):
        runtime,states=self.runtime()
        states[c.ENTITIES["load"]].last_reported-=timedelta(seconds=120)
        self.assertTrue(self.sample(runtime)["errors"])
        runtime,states=self.runtime()
        states[c.ENTITIES["load"]].last_reported-=timedelta(seconds=35)
        self.assertTrue(any("skew" in error for error in self.sample(runtime)["errors"]))
        states[c.ENTITIES["load"]].attributes["unit_of_measurement"]="W"
        self.assertTrue(any("unit" in error for error in self.sample(runtime)["errors"]))


class Marker:
    def __init__(self,key,**kwargs):self.key,self.options=key,kwargs


class FakeFlow:
    def __init_subclass__(cls,**kwargs):pass
    async def async_set_unique_id(self,value):self.unique_id=value
    def _abort_if_unique_id_configured(self):pass
    def async_show_form(self,**kwargs):return {"type":"form",**kwargs}
    def async_show_menu(self,**kwargs):return {"type":"menu",**kwargs}
    def async_create_entry(self,**kwargs):return {"type":"create_entry",**kwargs}
    def async_abort(self,**kwargs):return {"type":"abort",**kwargs}


class FlowTests(unittest.IsolatedAsyncioTestCase):
    """Real async flow methods exercised with small HA/selector substitutes."""
    @classmethod
    def setUpClass(cls):
        modules={}
        def module(name):
            m=types.ModuleType(name);modules[name]=m;return m
        vol=module("probatio");vol.Required=vol.Optional=Marker;vol.Schema=lambda data:data
        ha=module("homeassistant");entries=module("homeassistant.config_entries")
        entries.ConfigFlow=entries.OptionsFlow=FakeFlow;ha.config_entries=entries
        core=module("homeassistant.core");core.callback=lambda fn:fn
        helpers=module("homeassistant.helpers");selectors=module("homeassistant.helpers.selector")
        selectors.__getattr__=lambda name: (lambda *args,**kwargs:{"selector":name,"args":args,"config":kwargs})
        helpers.selector=selectors
        cls.previous={name:sys.modules.get(name) for name in modules}
        sys.modules.update(modules)
        spec=importlib.util.spec_from_file_location("solar_finance.config_flow",ROOT/"config_flow.py")
        cls.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.module)

    @classmethod
    def tearDownClass(cls):
        for name,old in cls.previous.items():
            if old is None:sys.modules.pop(name,None)
            else:sys.modules[name]=old

    async def asyncSetUp(self):
        import tempfile
        from pathlib import Path
        from solar_finance.ledger import Ledger
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.ledger=Ledger(Path(self.folder.name)/"ledger.sqlite3");self.ledger.initialize()
        async def executor(fn,*args):return fn(*args)
        self.flow=self.module.SolarFinanceOptions()
        self.flow.config_entry=types.SimpleNamespace(entry_id="test",options={},data={"sources":c.ENTITIES})
        self.flow.hass=types.SimpleNamespace(data={c.DOMAIN:{"test":types.SimpleNamespace(ledger=self.ledger)}},async_add_executor_job=executor)

    async def test_setup_sources_and_options_menu(self):
        form=await self.module.SolarFinanceFlow().async_step_user()
        self.assertEqual(len(form["data_schema"]),8)
        menu=await self.flow.async_step_init()
        self.assertIn("activate_export",menu["menu_options"])
        evidence=await self.flow.async_step_evidence()
        keys={marker.key for marker in evidence["data_schema"]}
        self.assertIn("baseline_import_gbp_kwh",keys)
        self.assertIn("loss_evidence",keys)

    async def test_simple_activation_date_time_and_explicit_pence(self):
        form=await self.flow.async_step_activate_export()
        keys={marker.key for marker in form["data_schema"]}
        self.assertTrue({"date","time","off_peak_rate","evidence"} <= keys)
        saved=await self.flow.async_step_activate_export({"date":"2026-10-28","time":"16:30:00","off_peak_rate":"3.00","standard_rate":"9.76","peak_rate":"27.70","standing_rate":"0","currency_unit":"pence","assumed":False,"evidence":"TEST dated export confirmation"})
        self.assertEqual(saved["type"],"create_entry")
        edits=saved["data"]["tariff_edits"]
        self.assertEqual(edits[0]["effective_from"],"2026-10-28T16:30:00+00:00")
        self.assertEqual(edits[0]["bands"][1][2],"0.03")
        self.assertEqual(edits[0]["rate_unit"],"GBP_per_kWh")
        self.ledger.configure({},[],edits)
        self.assertEqual(e.tariff_at(self.ledger.tariff_versions(),"export",e.timestamp("2026-10-28T17:00:00Z"))["template"],"flux_export_planned")

    async def test_correction_is_revision_not_mutation(self):
        record=next(v for v in self.ledger.tariff_versions() if v["template"]=="flux_import")
        form=await self.flow.async_step_tariff_history({"record":record["id"]})
        self.assertEqual(form["step_id"],"edit_tariff")
        saved=await self.flow.async_step_edit_tariff({"date":"2026-09-28","time":"00:00:00","off_peak_rate":".10","standard_rate":".30","peak_rate":".40","standing_rate":".45","currency_unit":"GBP","assumed":True,"evidence":"TEST revised assumption"})
        edit=saved["data"]["tariff_edits"][0]
        self.assertEqual(edit["replaces"],record["id"])
        self.ledger.configure({},[],[edit])
        self.assertEqual(len(self.ledger.versions("tariff")),4)
        self.assertEqual(len(self.ledger.tariff_versions()),3)

    async def test_invalid_clock_and_empty_confirmation_do_not_save(self):
        form=await self.flow.async_step_activate_export({"date":"2026-10-25","time":"01:30:00","off_peak_rate":".03","standard_rate":".08","peak_rate":".20","standing_rate":"0","currency_unit":"GBP","assumed":False,"evidence":""})
        self.assertEqual(form["type"],"form")
        self.assertEqual(form["errors"]["base"],"invalid_tariffs")


if __name__=="__main__":unittest.main(verbosity=2)
