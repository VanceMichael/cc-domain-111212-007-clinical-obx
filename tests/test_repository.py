import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import app
from hl7 import parse_message, observation_identity, identity_key
from repository import ObservationStore
from sampledata import samples


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.fixtures = samples()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / 'observations.sqlite3')

    def store(self):
        return ObservationStore(self.db)

    def identity_of(self, fixture, index=0):
        parsed = parse_message(self.fixtures[fixture])
        return observation_identity(parsed, parsed.observations[index])

    def test_ingest_returns_typed_results_and_reasons(self):
        with self.store() as repo:
            result = repo.ingest('local-partner', self.fixtures['sample.hl7'])
        self.assertFalse(result['duplicate'])
        self.assertEqual(result['new_versions'], 2)
        with self.store() as repo:
            current = repo.current(self.identity_of('sample.hl7', 0))
        self.assertEqual(current['status'], 'F')
        self.assertEqual(current['values'][0]['number'], 5.1)
        self.assertEqual(current['unit_raw'], 'mmol/L')
        self.assertEqual(current['reference_range_raw'], '3.9-6.1')

    def test_resending_identical_message_adds_no_results(self):
        with self.store() as repo:
            first = repo.ingest('local-partner', self.fixtures['sample.hl7'])
            second = repo.ingest('local-partner', self.fixtures['sample.hl7'])
            third = repo.ingest('local-partner', self.fixtures['sample.hl7'])
            self.assertTrue(second['duplicate'])
            self.assertTrue(third['duplicate'])
            self.assertEqual(second['message_id'], first['message_id'])
            self.assertEqual(repo.message_count(), 1)
            history = repo.history()
            self.assertEqual(len(history), 2)
            self.assertEqual({h['version'] for h in history}, {1})

    def test_correction_keeps_old_value_and_writes_new_version(self):
        identity = self.identity_of('sample.hl7', 0)
        with self.store() as repo:
            repo.ingest('local-partner', self.fixtures['sample.hl7'])
            repo.ingest('local-partner', self.fixtures['correction.hl7'])
            current = repo.current(identity)
            versions = repo.history(identity=identity)
        self.assertEqual(current['version'], 2)
        self.assertEqual(current['state'], 'corrected')
        self.assertEqual(current['status'], 'C')
        self.assertEqual(current['values'][0]['number'], 5.3)
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0]['values'][0]['number'], 5.1)
        self.assertEqual(versions[1]['values'][0]['number'], 5.3)
        self.assertEqual([v['status'] for v in versions], ['F', 'C'])

    def test_cancel_targets_only_the_sub_observation(self):
        with self.store() as repo:
            repo.ingest('local-partner', self.fixtures['subids.hl7'])
            repo.ingest('local-partner', self.fixtures['subid_cancel.hl7'])
            observations = repo.list_observations()
            self.assertEqual(len(observations), 2)
            states = {o['sub_id']: o['state'] for o in observations}
            self.assertEqual(states, {'1': 'active', '2': 'cancelled'})
            cancelled_id = next(o for o in observations if o['sub_id'] == '2')
            history = repo.history(identity={
                'sending_facility': cancelled_id['sending_facility'],
                'order_number': cancelled_id['order_number'],
                'coding_system': cancelled_id['coding_system'],
                'identifier': cancelled_id['identifier'],
                'sub_id': '2'})
            self.assertEqual([h['status'] for h in history], ['F', 'D'])
            # 撤销不删除行，旧值仍可回溯
            self.assertEqual(history[0]['values'][0]['number'], 1000)
            active = repo.list_observations(include_cancelled=False)
            self.assertEqual([o['sub_id'] for o in active], ['1'])

    def test_unknown_type_and_bad_number_are_quarantined_with_context(self):
        with self.store() as repo:
            repo.ingest('local-partner', self.fixtures['edge_values.hl7'])
            listed = repo.list_observations()
            self.assertEqual(len(listed), 4)
            # 空字段与协议空值是合法语义，不隔离；非法 NM 与未知类型隔离
            states = {o['sub_id']: o['state'] for o in listed}
            self.assertEqual(states, {'1': 'active', '2': 'active',
                                      '3': 'quarantined', '4': 'quarantined'})
            quarantined = repo.history()
        rejected = [h for h in quarantined
                    if any(not v['adopted'] for v in h['values'])]
        self.assertEqual(len(rejected), 2)
        for version in rejected:
            for value in version['values']:
                if not value['adopted']:
                    self.assertTrue(value['reason'])
            self.assertIn('OBX', version['raw_segment'])

    def test_segment_number_cannot_deduplicate_observations(self):
        # 两条报文都用 OBX|1 与相同 GLU 项目，但申请单不同：必须是两个观察。
        with self.store() as repo:
            repo.ingest('local-partner', self.fixtures['sample.hl7'])
            repo.ingest('local-partner', self.fixtures['other_order.hl7'])
            keys = {o['identity_key'] for o in repo.list_observations()}
        self.assertEqual(len(keys), 3)  # sample 的 GLU+NOTE 与 other_order 的 GLU
        order_keys = {k for k in keys if 'GLU' in k}
        self.assertEqual(len(order_keys), 2)

    def test_patient_identifier_cannot_deduplicate_or_wipe_results(self):
        # sample 与 other_order 同属 synthetic-1，但申请单不同，结果必须并存；
        # 撤销其中一个观察也不能影响同一患者的其他结果。
        with self.store() as repo:
            repo.ingest('local-partner', self.fixtures['sample.hl7'])
            repo.ingest('local-partner', self.fixtures['other_order.hl7'])
            patient_history = repo.history(patient_id='synthetic-1')
            self.assertEqual(len(patient_history), 3)
            observations = repo.list_observations()
            self.assertTrue(all('patient_name' not in o for o in observations))
            self.assertTrue(all('DOE' not in str(o) for o in observations))

    def test_repeated_values_and_custom_delimiters_round_trip(self):
        with self.store() as repo:
            repo.ingest('remote-lab', self.fixtures['alternate_escape.hl7'])
            identity = self.identity_of('alternate_escape.hl7', 0)
            current = repo.current(identity)
        self.assertEqual(len(current['values']), 2)
        self.assertEqual([v['number'] for v in current['values']], [4.0, 4.2])
        text_id = self.identity_of('alternate_escape.hl7', 1)
        with self.store() as repo:
            text_current = repo.current(text_id)
        self.assertEqual([v['text'] for v in text_current['values']],
                         ['line!break', 'a%b'])
        # 原始 OBX 段保留在版本里
        self.assertIn('OBX*2*ST', text_current['raw_segment'])

    def test_units_are_never_converted(self):
        with self.store() as repo:
            repo.ingest('local-partner', self.fixtures['sample.hl7'])
            identity = self.identity_of('sample.hl7', 0)
            current = repo.current(identity)
        self.assertEqual(current['unit_raw'], 'mmol/L')
        # 记录中不存在任何换算字段
        self.assertNotIn('converted_unit', current)
        self.assertNotIn('converted_value', current)

    def test_same_bytes_from_different_sources_are_separate_messages(self):
        with self.store() as repo:
            a = repo.ingest('source-a', self.fixtures['sample.hl7'])
            b = repo.ingest('source-b', self.fixtures['sample.hl7'])
            self.assertNotEqual(a['message_id'], b['message_id'])
            self.assertEqual(repo.message_count(), 2)
            # 但业务观察身份相同：第二条报文的版本挂到同一观察上
            glucose = repo.current(self.identity_of('sample.hl7', 0))
            self.assertEqual(glucose['version'], 2)

    def test_empty_source_rejected_without_writing(self):
        with self.store() as repo:
            with self.assertRaises(ValueError):
                repo.ingest(' ', self.fixtures['sample.hl7'])
            self.assertEqual(repo.message_count(), 0)
            self.assertEqual(repo.list_observations(), [])

    def test_cli_collect_observations_and_history(self):
        directory = Path(self.temp.name)
        message_path = directory / 'sample.hl7'
        message_path.write_bytes(self.fixtures['sample.hl7'])
        database = str(directory / 'cli.sqlite3')

        def run(*argv):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = app.main(list(argv))
            self.assertEqual(code, 0, argv)
            return json.loads(buffer.getvalue())

        first = run('collect', str(message_path), '--database', database,
                    '--source', 'cli')
        again = run('collect', str(message_path), '--database', database,
                    '--source', 'cli')
        self.assertFalse(first['duplicate'])
        self.assertTrue(again['duplicate'])
        listing = run('observations', '--database', database)
        self.assertEqual(len(listing), 2)
        self.assertTrue(all('name' not in item for item in listing))
        history = run('history', '--database', database)
        self.assertEqual(len(history), 2)
        self.assertTrue(all('name' not in item for item in history))

    def test_cli_rejects_non_oru_without_success_output(self):
        directory = Path(self.temp.name)
        bad_path = directory / 'bad.hl7'
        bad_path.write_bytes(b'MSH|^~\\&|LAB|LOCAL|OBS|LOCAL||||ADT^A01|x|P|2.5\r')
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = app.main(['collect', str(bad_path), '--database',
                             str(directory / 'x.sqlite3'), '--source', 'cli'])
        self.assertEqual(code, 2)
        self.assertIn('Hl7Error', buffer.getvalue())


if __name__ == '__main__':
    unittest.main()
