#!/usr/bin/python3

from hashlib import sha1
import json
from pathlib import Path

from brownie.network.history import TxHistory, _ContractHistory
from brownie.project import build
from brownie.project.scripts import get_ast_hash
from brownie.test import coverage
from brownie._config import ARGV


STATUS_SYMBOLS = {
    'passed': '.',
    'skipped': 's',
    'failed': 'F'
}

STATUS_TYPES = {
    '.': "passed",
    's': "skipped",
    'F': "failed",
    'E': "error",
    'x': "xfailed",
    'X': "xpassed"
}

history = TxHistory()
_contracts = _ContractHistory()


class TestManager:

    def __init__(self, path):
        self.project_path = path
        self.active_path = None
        self.count = 0
        self.results = None
        self.isolated = set()
        self.conf_hashes = dict(
            (self._path(i.parent), get_ast_hash(i)) for i in Path(path).glob('tests/**/conftest.py')
        )
        try:
            with path.joinpath('build/tests.json').open() as fp:
                hashes = json.load(fp)
        except (FileNotFoundError, json.decoder.JSONDecodeError):
            hashes = {'tests': {}, 'contracts': {}, 'tx': {}}

        self.tests = dict(
            (k, v) for k, v in hashes['tests'].items() if
            Path(k).exists() and self._get_hash(k) == v['sha1']
        )
        self.contracts = dict((k, v['bytecodeSha1']) for k, v in build.items() if v['bytecode'])
        changed_contracts = set(
            k for k, v in hashes['contracts'].items() if
            k not in self.contracts or v != self.contracts[k]
        )
        if changed_contracts:
            for txhash, coverage_eval in hashes['tx'].items():
                if not changed_contracts.intersection(coverage_eval.keys()):
                    coverage.add_cached(txhash, coverage_eval)
            self.tests = dict(
                (k, v) for k, v in self.tests.items() if v['isolated'] is not False
                and not changed_contracts.intersection(v['isolated'])
            )
        else:
            for txhash, coverage_eval in hashes['tx'].items():
                coverage.add_cached(txhash, coverage_eval)

    def _path(self, path):
        return str(Path(path).absolute().relative_to(self.project_path))

    def set_isolated_modules(self, paths):
        self.isolated = set(self._path(i) for i in paths)

    def _get_hash(self, path):
        hash_ = get_ast_hash(path)
        for confpath in filter(lambda k: k in path, sorted(self.conf_hashes)):
            hash_ += self.conf_hashes[confpath]
        return sha1(hash_.encode()).hexdigest()

    def check_updated(self, path):
        path = self._path(path)
        if path not in self.tests or not self.tests[path]['isolated']:
            return False
        if ARGV['coverage'] and not self.tests[path]['coverage']:
            return False
        for txhash in self.tests[path]['txhash']:
            coverage.add_from_cached(txhash, False)
        return True

    def module_completed(self, path):
        path = self._path(path)
        isolated = False
        if path in self.isolated:
            isolated = [i for i in _contracts.dependencies() if i in self.contracts]
        txhash = coverage.get_and_clear_active()
        if not ARGV['coverage'] and (path in self.tests and self.tests[path]['coverage']):
            txhash = self.tests[path]['txhash']
        self.tests[path] = {
            'sha1': self._get_hash(path),
            'isolated': isolated,
            'coverage': ARGV['coverage'] or (path in self.tests and self.tests[path]['coverage']),
            'txhash': txhash,
            'results': "".join(self.results)
        }

    def save_json(self):
        txhash = set(x for v in self.tests.values() for x in v['txhash'])
        coverage_eval = dict((k, v) for k, v in coverage.get_all().items() if k in txhash)
        report = {
            'tests': self.tests,
            'contracts': self.contracts,
            'tx': coverage_eval
        }
        with self.project_path.joinpath('build/tests.json').open('w') as fp:
            json.dump(report, fp, indent=2, sort_keys=True, default=sorted)

    def set_active(self, path):
        path = self._path(path)
        if path == self.active_path:
            self.count += 1
            return
        self.active_path = path
        self.count = 0
        if path in self.tests and ARGV['update']:
            self.results = list(self.tests[path]['results'])
        else:
            self.results = []

    def check_status(self, report):
        if report.when == "setup":
            self._skip = report.skipped
            if len(self.results) < self.count+1:
                self.results.append("s" if report.skipped else None)
            if report.failed:
                self.results[self.count] = "E"
                return "error", "E", "ERROR"
            return "", "", ""
        if report.when == "teardown":
            if report.failed:
                self.results[self.count] = "E"
                return "error", "E", "ERROR"
            elif self._skip:
                report.outcome = STATUS_TYPES[self.results[self.count]]
                return "skipped", "s", "SKIPPED"
            return "", "", ""
        if hasattr(report, "wasxfail"):
            self.results[self.count] = 'x' if report.skipped else 'X'
            if report.skipped:
                return "xfailed", "x", "XFAIL"
            elif report.passed:
                return "xpassed", "X", "XPASS"
        self.results[self.count] = STATUS_SYMBOLS[report.outcome]
        return report.outcome, STATUS_SYMBOLS[report.outcome], report.outcome.upper()
