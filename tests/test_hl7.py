import unittest

from hl7 import parse_message, observation_identity, identity_key, Hl7Error
from sampledata import samples


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.fixtures = samples()

    def parse(self, name):
        return parse_message(self.fixtures[name])

    def test_numeric_text_and_coded_value_types(self):
        parsed = self.parse('sample.hl7')
        self.assertEqual(parsed.message_type, 'ORU^R01')
        self.assertEqual(parsed.sending_facility, 'LOCAL')
        self.assertEqual(parsed.order_number, 'order-1')
        nm, st = parsed.observations
        self.assertTrue(nm.value_type_known)
        self.assertEqual(nm.values[0].kind, 'number')
        self.assertEqual(nm.values[0].number, 5.1)
        self.assertTrue(nm.values[0].adopted)
        self.assertEqual(nm.unit_raw, 'mmol/L')
        self.assertEqual(nm.reference_range_raw, '3.9-6.1')
        # 单位与参考范围原样留证
        self.assertEqual(st.value_type, 'ST')
        self.assertEqual(st.values[0].kind, 'text')

    def test_escape_decoded_with_custom_escape_character(self):
        parsed = self.parse('sample.hl7')
        st = parsed.observations[1]
        # \F\ 在标准分隔符下还原为字段分隔符 |
        self.assertEqual(st.values[0].text, 'A|B')
        self.assertEqual(st.values[0].raw, 'A\\F\\B')
        remote = self.parse('alternate_escape.hl7')
        note = remote.observations[1]
        self.assertEqual(remote.encoding.field, '*')
        self.assertEqual(remote.encoding.repetition, '%')
        self.assertEqual(remote.encoding.escape, '!')
        # !E! -> 转义符本身；!R! -> 重复分隔符字符（作为文本，不拆值）
        self.assertEqual(note.values[0].text, 'line!break')
        self.assertEqual(note.values[1].text, 'a%b')

    def test_repeated_values_are_separate_typed_values(self):
        parsed = self.parse('sample.hl7')
        st = parsed.observations[1]
        self.assertEqual(len(st.values), 2)
        self.assertEqual([v.text for v in st.values], ['A|B', 'second'])
        remote = self.parse('alternate_escape.hl7')
        nm = remote.observations[0]
        self.assertEqual(len(nm.values), 2)
        self.assertEqual([v.number for v in nm.values], [4.0, 4.2])
        # 原始重复结构仍可回溯
        self.assertEqual([v.raw for v in nm.values], ['4', '4.2'])

    def test_custom_delimiters_on_alternate_fixture(self):
        parsed = self.parse('alternate.hl7')
        self.assertEqual((parsed.encoding.field, parsed.encoding.component,
                          parsed.encoding.repetition, parsed.encoding.escape,
                          parsed.encoding.subcomponent),
                         ('*', '$', '%', '!', '?'))
        obx = parsed.observations[0]
        self.assertEqual(obx.identifier, 'X')
        self.assertEqual(obx.coding_system, 'L')
        self.assertEqual(obx.values[0].number, 7.0)

    def test_sub_observations_have_distinct_identity(self):
        parsed = self.parse('subids.hl7')
        identities = [observation_identity(parsed, o) for o in parsed.observations]
        self.assertEqual([i['sub_id'] for i in identities], ['1', '2'])
        keys = {identity_key(i) for i in identities}
        self.assertEqual(len(keys), 2)
        coded = parsed.observations[0]
        self.assertEqual(coded.values[0].kind, 'coded')
        self.assertEqual(coded.values[0].code['identifier'], 'ORG1')
        self.assertEqual(coded.values[0].code['coding_system'], 'L99')

    def test_empty_vs_protocol_null_vs_bad_number_vs_unknown_type(self):
        parsed = self.parse('edge_values.hl7')
        empty, null, bad, unknown = parsed.observations
        self.assertEqual(empty.values[0].kind, 'empty')
        self.assertIsNone(empty.values[0].number)
        self.assertIn('为空', empty.values[0].reason)
        self.assertEqual(null.values[0].kind, 'null')
        self.assertIn('协议空值', null.values[0].reason)
        self.assertEqual(bad.values[0].kind, 'rejected')
        self.assertFalse(bad.values[0].adopted)
        self.assertEqual(bad.values[0].text, 'oops')
        self.assertIn('不是合法数值', bad.values[0].reason)
        self.assertEqual(unknown.value_type, 'ZZ')
        self.assertFalse(unknown.value_type_known)
        self.assertEqual(unknown.values[0].kind, 'unknown')
        self.assertFalse(unknown.values[0].adopted)
        self.assertIn('ZZ', unknown.values[0].reason)
        # 报文上下文保留
        self.assertTrue(unknown.raw_segment.startswith('OBX|4|ZZ|'))

    def test_patient_name_never_collected(self):
        # 构造一个在 PID-5 携带虚构姓名的报文：类型化结果只能使用 PID-3 合成标识
        raw = (b'MSH|^~\\&|LAB|LOCAL|OBS|LOCAL|202611131000||ORU^R01|m-4|P|2.5\r'
               b'PID|1||synthetic-2|||DOE^JOHN\r'
               b'OBR|1|order-2||CULT^Culture^L\r'
               b'OBX|1|CE|CULT^Culture^L|1|ORG1^E.coli^L99||||||F\r')
        parsed = parse_message(raw)
        self.assertEqual(parsed.patient_id, 'synthetic-2')
        # 类型化观察与身份中没有任何姓名字段
        for observation in parsed.observations:
            blob = repr(observation)
            self.assertNotIn('JOHN', blob)
            self.assertNotIn('DOE', blob)
            identity = observation_identity(parsed, observation)
            self.assertNotIn('name', {k.lower() for k in identity})
        # ParsedMessage 的业务字段（结构留证 segments 与原件 raw 除外，
        # 原件由留存库单独管理、不进查询界面）不含姓名
        business = {k: v for k, v in parsed.__dict__.items()
                    if k not in ('segments', 'raw')}
        self.assertNotIn('JOHN', repr(business))
        self.assertNotIn('DOE', repr(business))

    def test_rejects_non_oru_and_malformed_inputs(self):
        bad_type = (b'MSH|^~\\&|LAB|LOCAL|OBS|LOCAL|202611120900||ADT^A01|x|P|2.5\r'
                    b'PID|1||synthetic-1\r')
        with self.assertRaises(Hl7Error):
            parse_message(bad_type)
        with self.assertRaises(Hl7Error):
            parse_message(self.fixtures['invalid.hl7'])
        with self.assertRaises(Hl7Error):
            parse_message(b'MSH|^~~&|LAB\r')
        with self.assertRaises(Hl7Error):
            parse_message(b'not a message at all')
        # 字段分隔符不得与编码字符混用
        with self.assertRaises(Hl7Error):
            parse_message(b'MSH||~\\&|LAB|LOCAL||||ORU^R01|x|P|2.5\r')
        # 非 UTF-8 字节不得静默替换
        with self.assertRaises(Hl7Error):
            parse_message(b'MSH|^~\xff&|LAB|LOCAL||||ORU^R01|x|P|2.5\r')

    def test_obx_without_order_is_quarantined_not_dropped(self):
        raw = (b'MSH|^~\\&|LAB|LOCAL|OBS|LOCAL|202611120900||ORU^R01|x|P|2.5\r'
               b'OBX|1|NM|GLU^Glucose^L|1|5||||||F\r')
        parsed = parse_message(raw)
        self.assertEqual(parsed.order_number, '')
        self.assertEqual(len(parsed.observations), 1)
        note = ' '.join(parsed.observations[0].notes)
        self.assertIn('OBR-2', note)
        # 报文上下文仍保留
        self.assertTrue(parsed.observations[0].raw_segment.startswith('OBX'))

    def test_deterministic_output(self):
        first = parse_message(self.fixtures['alternate_escape.hl7'])
        second = parse_message(self.fixtures['alternate_escape.hl7'])
        self.assertEqual(
            [identity_key(observation_identity(first, o)) for o in first.observations],
            [identity_key(observation_identity(second, o)) for o in second.observations])

    def test_highlight_escapes_stripped_and_unknown_escapes_retained(self):
        base = 'MSH|^~\\&|LAB|LOCAL|OBS|LOCAL|20261113||ORU^R01|m|P|2.5\r'
        raw = (base +
               'OBR|1|ord||H^H^L\r'
               'OBX|1|ST|H^H^L|s|a\\H\\hi\\N\\b||||||F\r').encode()
        value = parse_message(raw).observations[0].values[0]
        self.assertEqual(value.text, 'ahib')
        notes = value.parts[0]['subcomponents'][0]['notes']
        self.assertTrue(any('高亮' in note for note in notes))

        raw_unknown = (base +
                       'OBR|1|ord2||U^U^L\r'
                       'OBX|1|ST|U^U^L|s|a\\Z\\b||||||F\r').encode()
        unknown = parse_message(raw_unknown).observations[0].values[0]
        self.assertEqual(unknown.text, 'a\\Z\\b')
        self.assertTrue(any('未识别转义' in note
                            for note in unknown.parts[0]['subcomponents'][0]['notes']))


if __name__ == '__main__':
    unittest.main()
