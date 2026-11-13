"""类型化观察记录库。

在本地 SQLite 中保存消息原件与观察版本，保证：
* 相同报文重送不增加结果（按来源 + 原件摘要 + 消息控制号幂等）；
* 更正（C）产生新版本并保留旧值可回溯，撤销（D）只作用于复合身份
  指向的那一个观察，绝不删除同一患者的其他结果；
* 单位与参考范围原样留证，没有明确换算配置时不做任何单位换算；
* 查询输出不含患者姓名（解析层即不采集 PID-5）。
"""
from datetime import datetime, timezone
import hashlib
import json
import sqlite3

from hl7 import parse_message, observation_identity, identity_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    digest TEXT NOT NULL,
    message_control_id TEXT NOT NULL,
    message_type TEXT NOT NULL,
    raw BLOB NOT NULL,
    received_at TEXT NOT NULL,
    UNIQUE(source, digest)
);
CREATE TABLE IF NOT EXISTS observations(
    id INTEGER PRIMARY KEY,
    identity_key TEXT NOT NULL UNIQUE,
    sending_facility TEXT NOT NULL,
    order_number TEXT NOT NULL,
    coding_system TEXT NOT NULL,
    identifier TEXT NOT NULL,
    sub_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observation_versions(
    id INTEGER PRIMARY KEY,
    observation_id INTEGER NOT NULL REFERENCES observations(id),
    version INTEGER NOT NULL,
    message_id INTEGER NOT NULL REFERENCES messages(id),
    status TEXT NOT NULL,
    status_meaning TEXT,
    value_type TEXT NOT NULL,
    values_json TEXT NOT NULL,
    unit_raw TEXT NOT NULL,
    reference_range_raw TEXT NOT NULL,
    raw_segment TEXT NOT NULL,
    notes_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE(observation_id, message_id)
);
"""


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _value_to_dict(value):
    return {
        'kind': value.kind,
        'adopted': value.adopted,
        'raw': value.raw,
        'text': value.text,
        'number': value.number,
        'code': value.code,
        'reason': value.reason,
    }


class ObservationStore:
    def __init__(self, path):
        self.connection = sqlite3.connect(path, timeout=5)
        self.connection.execute('PRAGMA foreign_keys=ON')
        self.connection.executescript(SCHEMA)

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ---- 写入 ----------------------------------------------------------

    def ingest(self, source, raw):
        """类型化采集一条 ORU 报文；重复原件返回既有收件且不新增结果。"""
        if not source.strip():
            raise ValueError('来源标识不能为空')
        parsed = parse_message(raw)
        digest = hashlib.sha256(raw).hexdigest()
        now = _utc_now()
        with self.connection:
            existing = self.connection.execute(
                'SELECT id FROM messages WHERE source=? AND digest=?',
                (source, digest)).fetchone()
            if existing is not None:
                return {
                    'duplicate': True,
                    'message_id': existing[0],
                    'observations': [],
                    'new_versions': 0,
                }
            cur = self.connection.execute(
                'INSERT INTO messages(source,digest,message_control_id,'
                'message_type,raw,received_at) VALUES(?,?,?,?,?,?)',
                (source, digest, parsed.message_control_id, parsed.message_type,
                 bytes(raw), now))
            message_id = cur.lastrowid

            applied = []
            for observation in parsed.observations:
                identity = observation_identity(parsed, observation)
                key = identity_key(identity)
                row = self.connection.execute(
                    'SELECT id,current_version,state FROM observations '
                    'WHERE identity_key=?', (key,)).fetchone()
                if row is None:
                    observation_id = self._create_observation(
                        identity, parsed.patient_id, now)
                    version_no = 1
                else:
                    observation_id, version_no, _prev_state = row
                    # 同一报文对同一观察已登记过版本（重放保护的第二道闸）
                    dup_version = self.connection.execute(
                        'SELECT 1 FROM observation_versions '
                        'WHERE observation_id=? AND message_id=?',
                        (observation_id, message_id)).fetchone()
                    if dup_version is not None:
                        continue
                    version_no += 1
                    self.connection.execute(
                        'UPDATE observations SET updated_at=? WHERE id=?',
                        (now, observation_id))

                self._add_version(observation_id, version_no, message_id,
                                  observation, now)
                new_state = self._next_state(observation)
                self.connection.execute(
                    'UPDATE observations SET current_version=?,state=?,updated_at=?'
                    ' WHERE id=?',
                    (version_no, new_state, now, observation_id))
                applied.append({
                    'identity': identity,
                    'version': version_no,
                    'state': new_state,
                    'status': observation.status,
                })
        return {
            'duplicate': False,
            'message_id': message_id,
            'message_control_id': parsed.message_control_id,
            'observations': applied,
            'new_versions': len(applied),
        }

    def _create_observation(self, identity, patient_id, now):
        cur = self.connection.execute(
            'INSERT INTO observations(identity_key,sending_facility,order_number,'
            'coding_system,identifier,sub_id,patient_id,current_version,state,'
            'created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (identity_key(identity), identity['sending_facility'],
             identity['order_number'], identity['coding_system'],
             identity['identifier'], identity['sub_id'], patient_id,
             0, 'active', now, now))
        return cur.lastrowid

    def _add_version(self, observation_id, version_no, message_id, observation, now):
        self.connection.execute(
            'INSERT INTO observation_versions(observation_id,version,message_id,'
            'status,status_meaning,value_type,values_json,unit_raw,'
            'reference_range_raw,raw_segment,notes_json,recorded_at) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (observation_id, version_no, message_id, observation.status,
             observation.status_meaning, observation.value_type,
             json.dumps([_value_to_dict(v) for v in observation.values],
                        ensure_ascii=False, sort_keys=True),
             observation.unit_raw, observation.reference_range_raw,
             observation.raw_segment,
             json.dumps(list(observation.notes), ensure_ascii=False), now))

    @staticmethod
    def _next_state(observation):
        meaning = observation.status_meaning
        if meaning == 'cancelled':
            return 'cancelled'
        if meaning == 'correction':
            base = 'corrected'
        elif meaning == 'final':
            # 撤销后再次收到 F 视为重新生效
            base = 'active'
        else:
            # 未知 OBX-11 状态：不按 F/C/D 解释，隔离
            return 'quarantined'
        # 仅当存在被拒绝或未知类型的值时隔离该观察；空字段(empty)与
        # 协议空值(null)是来源约定认可的合法语义，不触发隔离。
        if any(value.kind in ('rejected', 'unknown')
               for value in observation.values):
            return 'quarantined'
        return base

    # ---- 查询（不输出姓名）--------------------------------------------

    def list_observations(self, include_cancelled=True):
        rows = self.connection.execute(
            'SELECT identity_key,sending_facility,order_number,coding_system,'
            'identifier,sub_id,patient_id,current_version,state,updated_at '
            'FROM observations ORDER BY id').fetchall()
        result = []
        for row in rows:
            item = {
                'identity_key': row[0],
                'sending_facility': row[1],
                'order_number': row[2],
                'coding_system': row[3],
                'identifier': row[4],
                'sub_id': row[5],
                'patient_id': row[6],
                'current_version': row[7],
                'state': row[8],
                'updated_at': row[9],
            }
            if not include_cancelled and item['state'] == 'cancelled':
                continue
            result.append(item)
        return result

    def history(self, identity=None, patient_id=None):
        """返回观察的全部版本（含更正前旧值与撤销记录），按观察与版本排序。"""
        sql = ('SELECT o.identity_key,o.sending_facility,o.order_number,'
               'o.coding_system,o.identifier,o.sub_id,o.patient_id,o.state,'
               'v.version,v.status,v.status_meaning,v.value_type,v.values_json,'
               'v.unit_raw,v.reference_range_raw,v.raw_segment,v.notes_json,'
               'v.recorded_at,v.message_id '
               'FROM observation_versions v JOIN observations o '
               'ON o.id=v.observation_id')
        clauses, params = [], []
        if identity is not None:
            clauses.append('o.identity_key=?')
            params.append(identity_key(identity))
        if patient_id is not None:
            clauses.append('o.patient_id=?')
            params.append(patient_id)
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        sql += ' ORDER BY o.id, v.version'
        out = []
        for row in self.connection.execute(sql, params):
            out.append({
                'identity_key': row[0],
                'sending_facility': row[1],
                'order_number': row[2],
                'coding_system': row[3],
                'identifier': row[4],
                'sub_id': row[5],
                'patient_id': row[6],
                'state': row[7],
                'version': row[8],
                'status': row[9],
                'status_meaning': row[10],
                'value_type': row[11],
                'values': json.loads(row[12]),
                'unit_raw': row[13],
                'reference_range_raw': row[14],
                'raw_segment': row[15],
                'notes': json.loads(row[16]),
                'recorded_at': row[17],
                'message_id': row[18],
            })
        return out

    def current(self, identity):
        key = identity_key(identity)
        row = self.connection.execute(
            'SELECT id,current_version,state FROM observations '
            'WHERE identity_key=?', (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        version = self.connection.execute(
            'SELECT version,status,status_meaning,value_type,values_json,'
            'unit_raw,reference_range_raw,raw_segment,notes_json,recorded_at,message_id '
            'FROM observation_versions WHERE observation_id=? AND version=?',
            (row[0], row[1])).fetchone()
        return {
            'identity_key': key,
            'state': row[2],
            'version': version[0],
            'status': version[1],
            'status_meaning': version[2],
            'value_type': version[3],
            'values': json.loads(version[4]),
            'unit_raw': version[5],
            'reference_range_raw': version[6],
            'raw_segment': version[7],
            'notes': json.loads(version[8]),
            'recorded_at': version[9],
            'message_id': version[10],
        }

    def message_count(self):
        return self.connection.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
