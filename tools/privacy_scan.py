"""Bounded source-only privacy guard; no private-file reads or HTTP calls.

Reports paths/rule/line only, never matched secret content. Not an anonymity proof.
"""
from pathlib import Path
import ipaddress
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.git', '__pycache__', '.venv', 'node_modules'}
ALLOWED_SUFFIXES = {'.py', '.js', '.cjs', '.json', '.md', '.yaml', '.yml', '.svg', '.txt'}
ALLOWED_URL_HOSTS = {
    'github.com', 'raw.githubusercontent.com', 'example.invalid',
    'app-eu.sigencloud.com', 'api.solcast.com.au', 'open-meteo.com',
    'www.apache.org', 'www.w3.org',
}
PATTERNS = {
    'embedded_data_payload': r'data:(?:image|application)/[^,;\s]+(?:;base64)?,',
    'long_encoded_payload': r'[A-Za-z0-9+/]{200,}={0,2}',
    'private_key_material': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'literal_registry_id': r'(?:device_id|config_entry_id)\s*(?:===|==|:)\s*[\x22\x27][A-Za-z0-9]{20,}[\x22\x27]',
    'credential_value': r'(?:api_key|access_token|refresh_token|password)\s*[\x22\x27]?\s*[:=]\s*[\x22\x27](?!synthetic|example|NEVER-SAVE|[\x22\x27])[^\x22\x27]{12,}[\x22\x27]',
    'email_address': r'\b[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',
    'literal_coordinate': r'[\x22\x27]?(?:latitude|longitude)[\x22\x27]?\s*[:=]\s*-?\d+\.\d+',
    'vehicle_vin': r'\b[A-HJ-NPR-Z0-9]{17}\b',
    # Standalone numeric identifier, not a decimal run inside a SHA-256 digest.
    'uk_meter_number': r'(?<![A-Za-z0-9])\d{13}(?![A-Za-z0-9])',
}

def scan():
    findings = []
    hosts = set()
    entity_names = set()
    files = 0
    for p in sorted(ROOT.rglob('*')):
        rel = p.relative_to(ROOT)
        if not p.is_file() or any(x in EXCLUDED for x in rel.parts):
            continue
        name = str(rel)
        if p.name != '.gitignore' and p.suffix not in ALLOWED_SUFFIXES:
            findings.append({'path': name, 'rule': 'non_source_file', 'line': 0})
            continue
        if any(re.search(r'(?i)(?:secret|private|backup|capture|profile)', x) for x in rel.parts):
            # Source functions may discuss privacy; files named for private data may not be shipped.
            if name != 'tools/privacy_scan.py':
                findings.append({'path': name, 'rule': 'sensitive_filename', 'line': 0})
        try:
            text = p.read_text()
        except UnicodeError:
            findings.append({'path': name, 'rule': 'binary_payload', 'line': 0})
            continue
        files += 1
        for rule, pattern in PATTERNS.items():
            for m in re.finditer(pattern, text, re.I if rule not in ('vehicle_vin',) else 0):
                findings.append({'path': name, 'rule': rule, 'line': text.count('\n', 0, m.start()) + 1})
        for m in re.finditer(r'https?://([^/\s\x22\x27<>`]+)', text):
            host = m.group(1).rstrip('.,);')
            hosts.add(host)
            if host not in ALLOWED_URL_HOSTS:
                findings.append({'path': name, 'rule': 'unreviewed_url_host', 'line': text.count('\n', 0, m.start()) + 1})
        for m in re.finditer(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])', text):
            try:
                ipaddress.ip_address(m.group())
            except ValueError:
                continue
            findings.append({'path': name, 'rule': 'literal_network_address', 'line': text.count('\n', 0, m.start()) + 1})
        entity_names.update(re.findall(r'\b(?:sensor|binary_sensor|number|select|switch|weather)\.[a-z][a-z0-9_]+', text))
    return {'status': 'PASS' if not findings else 'BLOCKED', 'files_scanned': files,
            'findings': findings, 'reviewed_public_url_hosts_present': sorted(hosts),
            'entity_literals_count': len(entity_names), 'rules': list(PATTERNS),
            'limitations': 'Bounded static source scan; no private inputs read; not a guarantee of anonymity or runtime safety.'}

if __name__ == '__main__':
    report = scan()
    print(json.dumps(report, indent=2))
    sys.exit(0 if report['status'] == 'PASS' else 1)
