"""ORU^R01 子集的类型化解析。

仅依据 docs/source-contract.md 的来源约定解释报文：
MSH-1/MSH-2 自定义分隔符、HL7 转义、重复值、组件/子组件，
以及 OBX-2 值类型（NM/ST/CE 与未知类型）对 OBX-5 的含义。

本模块只做纯解析，不做单位换算，不删除任何报文证据。
全部输入与机构均为本地虚构演练数据。
"""
from dataclasses import dataclass, field, replace
import re

# OBX-11 观察结果状态（来源约定 F/C/D，其余视为未知并隔离）
KNOWN_STATUS = {'F': 'final', 'C': 'correction', 'D': 'cancelled'}

# 来源约定的 OBX-2 值类型
KNOWN_VALUE_TYPES = {'NM', 'ST', 'CE'}

# HL7 数值：可选符号、整数/小数，可选科学计数法（HL7 2.5+ 允许）
_NUMBER_RE = re.compile(r'^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$')


class Hl7Error(ValueError):
    """报文不符合来源约定时抛出；调用方不得生成成功采集。"""


@dataclass(frozen=True)
class Encoding:
    field: str          # MSH-1
    component: str      # MSH-2 第 1 个
    repetition: str     # MSH-2 第 2 个
    escape: str         # MSH-2 第 3 个
    subcomponent: str   # MSH-2 第 4 个


@dataclass(frozen=True)
class Segment:
    name: str
    raw: str
    fields: tuple          # tuple[str | tuple, ...]，MSH 特殊处理
    line_no: int           # 报文中的段顺序号（仅定位用，不能做业务身份）

    def field(self, number):
        """按 HL7 字段号取值（1 基）；缺失返回 ''。

        MSH 段中 MSH-2 为编码字符定义、不占字段分隔位，故不存入 fields；
        fields[0]=MSH-1（字段分隔符本身），fields[1]=MSH-3。因此
        field(1) 取 index 0，MSH-n（n>=3）取 index n-1，与普通段一致，
        仅 field(2) 因无存储位而拒绝访问。
        """
        if number <= 0:
            raise ValueError('字段号从 1 开始')
        if self.name == 'MSH' and number == 2:
            raise ValueError('MSH-2 是编码字符定义，请使用 encoding')
        index = number - 1
        return self.fields[index] if index < len(self.fields) else ''

@dataclass(frozen=True)
class ObservationValue:
    """OBX-5 单个重复值的类型化解释。"""
    kind: str                 # number | coded | text | empty | null | unknown | rejected
    adopted: bool             # 是否可按来源约定采用
    raw: str                  # 该重复值的原文（反转义前的组件原文已在 parts 中留证）
    text: str | None = None   # 反转义后的文本表示
    number: float | None = None
    code: dict | None = None  # CE: identifier/text/coding_system/alt_*
    reason: str | None = None  # 无法采用的原因
    parts: tuple = ()         # 组件/子组件原文与反转义文本，保留报文上下文


@dataclass(frozen=True)
class Observation:
    """一个 OBX 段对应的类型化观察（子观察号不同即不同观察）。"""
    segment_index: int
    set_id: str
    value_type: str
    value_type_known: bool
    identifier: str
    identifier_text: str
    coding_system: str
    sub_id: str
    status: str
    status_meaning: str | None
    unit_raw: str
    reference_range_raw: str
    values: tuple            # tuple[ObservationValue, ...]
    raw_segment: str
    notes: tuple = ()


@dataclass(frozen=True)
class ParsedMessage:
    encoding: Encoding
    message_type: str
    message_control_id: str
    version_id: str
    sending_application: str
    sending_facility: str
    receiving_application: str
    receiving_facility: str
    patient_id: str          # 仅取 PID-3 合成患者标识；姓名不采集
    order_number: str
    segments: tuple
    observations: tuple
    raw: bytes = field(repr=False, default=b'')


def _split(text, delimiter):
    return tuple(text.split(delimiter)) if delimiter else (text,)


def _unescape(text, encoding):
    """按报文自定义转义字符解释 HL7 转义序列。

    标准分隔符转义（F/S/T/R/E）还原为对应字符；\\H\\、\\N\\ 为来源约定
    之外的高亮排版标记，剥离；无法识别的转义序列原样保留并附在说明里，
    绝不猜测含义。
    """
    if not text or encoding.escape not in text:
        return text, ()
    marker = re.escape(encoding.escape)
    pattern = re.compile(marker + r'(.*?)' + marker)
    notes = []

    def replace(match):
        seq = match.group(1)
        if seq == 'F':
            return encoding.field
        if seq == 'S':
            return encoding.component
        if seq == 'T':
            return encoding.subcomponent
        if seq == 'R':
            return encoding.repetition
        if seq == 'E':
            return encoding.escape
        if seq in ('H', 'N'):
            notes.append('剥离来源约定外的高亮转义 ' + encoding.escape + seq + encoding.escape)
            return ''
        notes.append('未识别转义序列原样保留：' + encoding.escape + seq + encoding.escape)
        return match.group(0)

    decoded = pattern.sub(replace, text)
    return decoded, tuple(notes)


def _parse_components(raw, encoding):
    """把字段原文拆成 (组件原文, 反转义文本, 子组件...) 的留证结构。"""
    components = []
    for component in _split(raw, encoding.component):
        sub_parts = []
        for sub in _split(component, encoding.subcomponent):
            text, notes = _unescape(sub, encoding)
            sub_parts.append({'raw': sub, 'text': text, 'notes': notes})
        components.append({'raw': component, 'subcomponents': tuple(sub_parts)})
    return tuple(components)


def _component_text(component, encoding):
    return encoding.component.join(
        part['text'] for part in component['subcomponents'])


def _field_text(raw, encoding):
    """字段（单值、无重复）反转义后的纯文本。"""
    decoded, _ = _unescape(raw, encoding)
    # 组件/子组件分隔符在文本化时还原为真实分隔结构，用分隔符连接
    parts = []
    for component in _split(decoded, encoding.component):
        parts.append(encoding.subcomponent.join(_split(component, encoding.subcomponent)))
    return encoding.component.join(parts)


def _parse_repeated(raw, encoding):
    """OBX-5 按重复分隔符拆分，每个重复保留组件/子组件原文。"""
    repeats = []
    for repeat in _split(raw, encoding.repetition):
        repeats.append(_parse_components(repeat, encoding))
    return tuple(repeats)


def _interpret_number(raw, encoding):
    text = _field_text(raw, encoding)
    stripped = text.strip()
    if stripped == '':
        return ObservationValue('empty', True, raw, text=None,
                                reason='字段为空：缺少数值，与协议空值不同')
    if stripped == '""':
        return ObservationValue('null', True, raw, text=None,
                                reason='协议空值（""）：明确无值，非缺失')
    if not _NUMBER_RE.match(stripped):
        return ObservationValue('rejected', False, raw, text=text,
                                reason='NM 类型但内容不是合法数值，隔离该重复值')
    return ObservationValue('number', True, raw, text=stripped, number=float(stripped))


def _interpret_text(raw, encoding):
    components = _parse_components(raw, encoding)
    text = encoding.component.join(_component_text(c, encoding) for c in components)
    notes = tuple(note for component in components
                  for sub in component['subcomponents'] for note in sub['notes'])
    if text == '':
        return ObservationValue('empty', True, raw, text=None,
                                reason='字段为空：缺少文本，与协议空值不同',
                                parts=components), notes
    if text == '""':
        return ObservationValue('null', True, raw, text=None,
                                reason='协议空值（""）：明确无值，非缺失',
                                parts=components), notes
    return ObservationValue('text', True, raw, text=text, parts=components), notes


def _interpret_coded(raw, encoding):
    components = _parse_components(raw, encoding)
    texts = [_component_text(c, encoding) for c in components]
    notes = tuple(note for component in components
                  for sub in component['subcomponents'] for note in sub['notes'])
    joined = encoding.component.join(texts)
    if joined == '':
        return ObservationValue('empty', True, raw, text=None,
                                reason='字段为空：缺少编码，与协议空值不同',
                                parts=components), notes
    if joined == '""':
        return ObservationValue('null', True, raw, text=None,
                                reason='协议空值（""）：明确无值，非缺失',
                                parts=components), notes
    get = lambda i: texts[i] if i < len(texts) else ''
    code = {
        'identifier': get(0),
        'text': get(1),
        'coding_system': get(2),
        'alt_identifier': get(3),
        'alt_text': get(4),
        'alt_coding_system': get(5),
    }
    adopted = bool(code['identifier'])
    reason = None if adopted else 'CE 缺少编码标识符，隔离该重复值'
    return ObservationValue('coded' if adopted else 'rejected', adopted, raw,
                            text=joined, code=code, reason=reason,
                            parts=components), notes


def _interpret_unknown(raw, encoding, value_type):
    components = _parse_components(raw, encoding)
    text = encoding.component.join(_component_text(c, encoding) for c in components)
    notes = tuple(note for component in components
                  for sub in component['subcomponents'] for note in sub['notes'])
    reason = '未知 OBX-2 值类型 %r：隔离到对应观察但保留报文上下文，不按字符串冒充' % value_type
    if text == '':
        return ObservationValue('empty', False, raw, text=None, reason=reason,
                                parts=components), notes
    if text == '""':
        return ObservationValue('null', False, raw, text=None, reason=reason,
                                parts=components), notes
    return ObservationValue('unknown', False, raw, text=text, reason=reason,
                            parts=components), notes


def _interpret_obx(segment, encoding):
    def raw_field(number):
        value = segment.field(number)
        return value if isinstance(value, str) else ''

    value_type = raw_field(2).strip().upper()
    identifier_field = raw_field(3)
    id_components = _split(identifier_field, encoding.component)
    identifier = id_components[0] if id_components else ''
    id_text = id_components[1] if len(id_components) > 1 else ''
    coding_system = id_components[2] if len(id_components) > 2 else ''
    if not identifier:
        raise Hl7Error('OBX-3 观察标识符缺失，无法建立观察身份（段 %d）' % segment.line_no)

    sub_id = raw_field(4)
    status_raw = raw_field(11).strip().upper()
    status_meaning = KNOWN_STATUS.get(status_raw)
    notes = []
    if status_raw and status_meaning is None:
        notes.append('未知 OBX-11 状态 %r：原样留证，不按 F/C/D 解释' % status_raw)

    value_raw = raw_field(5)
    values = []
    repeats = _parse_repeated(value_raw, encoding)
    for repeat_components in repeats:
        # 用字段分隔结构重组该重复的原文，保持与输入一致的组件文本
        repeat_raw = encoding.component.join(c['raw'] for c in repeat_components)
        if value_type == 'NM':
            values.append(_interpret_number(repeat_raw, encoding))
        elif value_type == 'ST':
            value, value_notes = _interpret_text(repeat_raw, encoding)
            values.append(value)
            notes.extend(value_notes)
        elif value_type == 'CE':
            value, value_notes = _interpret_coded(repeat_raw, encoding)
            values.append(value)
            notes.extend(value_notes)
        else:
            value, value_notes = _interpret_unknown(repeat_raw, encoding, value_type)
            values.append(value)
            notes.extend(value_notes)

    return Observation(
        segment_index=segment.line_no,
        set_id=raw_field(1),
        value_type=value_type,
        value_type_known=value_type in KNOWN_VALUE_TYPES,
        identifier=identifier,
        identifier_text=id_text,
        coding_system=coding_system,
        sub_id=sub_id,
        status=status_raw,
        status_meaning=status_meaning,
        unit_raw=raw_field(6),
        reference_range_raw=raw_field(7),
        values=tuple(values),
        raw_segment=segment.raw,
        notes=tuple(notes),
    )


def _split_segments(text):
    # 同时容忍演练中的 CR 与 CRLF/LF；空行忽略
    normalized = text.replace('\r\n', '\r').replace('\n', '\r')
    return [line for line in normalized.split('\r') if line != '']


def parse_message(data):
    """解析 ORU^R01 字节串，返回类型化 ParsedMessage；不合规抛 Hl7Error。"""
    if not isinstance(data, (bytes, bytearray)):
        raise Hl7Error('输入必须是原始字节')
    try:
        text = bytes(data).decode('utf8')
    except UnicodeDecodeError as error:
        raise Hl7Error('报文不是合法 UTF-8：%s' % error) from error
    if not text.startswith('MSH') or len(text) < 8:
        raise Hl7Error('不是 HL7 MSH 起始报文')

    field_sep = text[3]
    encoding_chars = text[4:8]
    if len(encoding_chars) != 4 or field_sep in encoding_chars:
        raise Hl7Error('MSH-2 必须依次定义组件、重复、转义、子组件四个互异字符')
    component_sep, repetition_sep, escape_sep, subcomponent_sep = encoding_chars
    if len(set(encoding_chars)) != 4:
        raise Hl7Error('MSH-2 四个编码字符必须互异')
    encoding = Encoding(field_sep, component_sep, repetition_sep,
                        escape_sep, subcomponent_sep)

    lines = _split_segments(text)
    segments = []
    for line_no, line in enumerate(lines):
        tag = line[:3]
        if len(tag) != 3 or not tag.isalpha():
            raise Hl7Error('段标识必须是三字符（第 %d 段：%r）' % (line_no, line[:8]))
        if line_no == 0:
            # MSH：MSH-1 是字段分隔符本身；MSH-2 是四个编码字符，紧跟其后且
            # 不占用字段分隔位；其余字段按字段分隔符切分。
            # fields[0]=MSH-1，fields[1]=MSH-3，故 field(n) 仍取 index n-1。
            rest = line[3 + 1 + 4:]   # 跳过 "MSH" + 字段分隔符 + 4 个编码字符
            fields = (field_sep,) + tuple(rest.split(field_sep))
        else:
            fields = tuple(line.split(field_sep))[1:]
        segments.append(Segment(tag, line, fields, line_no))

    msh = segments[0]
    message_type_field = msh.field(9)
    type_components = _split(message_type_field, component_sep)
    message_type = type_components[0] if type_components else ''
    trigger = type_components[1] if len(type_components) > 1 else ''
    if (message_type, trigger) != ('ORU', 'R01'):
        raise Hl7Error('仅支持来源约定 ORU^R01，收到 %s^%s' % (message_type, trigger))
    observations = []
    patient_id = ''
    order_number = ''
    pending_order = ''
    missing_order_notes = []
    for segment in segments[1:]:
        if segment.name == 'PID':
            patient_id = segment.field(3) if isinstance(segment.field(3), str) else ''
        elif segment.name == 'OBR':
            pending_order = segment.field(2) if isinstance(segment.field(2), str) else ''
            order_number = order_number or pending_order
        elif segment.name == 'OBX':
            # 来源约定要求 OBR-2 申请号；缺失时不丢弃报文，而是以空申请号
            # 隔离该观察并保留上下文，由记录库的无法采用原因体现。
            if not pending_order:
                missing_order_notes.append(
                    'OBX 前缺少 OBR-2 申请号（段 %d）：以空申请号隔离留证'
                    % segment.line_no)
            observations.append(_interpret_obx(segment, encoding))
    if missing_order_notes:
        extra = tuple(missing_order_notes)
        observations = tuple(replace(observation,
                                     notes=observation.notes + extra)
                             for observation in observations)
    if not order_number:
        # 无 OBR 时仍允许采集（隔离），但申请号为空字符串
        order_number = ''

    def msh_text(number):
        value = msh.field(number)
        return value if isinstance(value, str) else ''

    return ParsedMessage(
        encoding=encoding,
        message_type=message_type + '^' + trigger,
        message_control_id=msh_text(10),
        version_id=msh_text(12),
        sending_application=msh_text(3),
        sending_facility=msh_text(4),
        receiving_application=msh_text(5),
        receiving_facility=msh_text(6),
        patient_id=patient_id,
        order_number=order_number,
        segments=tuple(segments),
        observations=tuple(observations),
        raw=bytes(data),
    )

def observation_identity(parsed, observation):
    """观察复合身份：发送机构 + 申请单 + 项目编码系统/编码 + 子观察号。

    段号与患者号都不参与身份（见 docs/observation-identity.md）。
    """
    return {
        'sending_facility': parsed.sending_facility,
        'order_number': parsed.order_number,
        'coding_system': observation.coding_system,
        'identifier': observation.identifier,
        'sub_id': observation.sub_id,
    }


def identity_key(identity):
    return '|'.join((identity['sending_facility'], identity['order_number'],
                     identity['coding_system'], identity['identifier'],
                     identity['sub_id']))
