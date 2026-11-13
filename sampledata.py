"""本地虚构演练报文。全部患者与机构均为合成数据，不含真实个人信息。

既有 sample/invalid/alternate 夹具的段结构保持稳定（现有结构检查测试依赖
段数与段标识）；新增夹具覆盖来源约定中的重复值、自定义分隔符转义、
子观察、撤销、未知类型与空值语义。

OBX 字段位置（来源约定）：1 集号、2 值类型、3 观察标识、4 子观察号、
5 值、6 单位、7 参考范围、8-10 留空、11 状态(F/C/D)。即每个 OBX 段
共 11 个字段，由 10 个字段分隔符隔开。
"""


def samples():
    message = ('MSH|^~\\&|LAB|LOCAL|OBS|LOCAL|202611120900||ORU^R01|m-1|P|2.5\r'
               'PID|1||synthetic-1\r'
               'OBR|1|order-1||GLU^Glucose^L\r'
               # 字段：1集 2型 3标识 4子号 5值 6单位 7范围 8空 9空 10空 11状态
               'OBX|1|NM|GLU^Glucose^L|a|5.1|mmol/L|3.9-6.1||||F\r'
               'OBX|2|ST|NOTE^Note^L|b|A\\F\\B~second||||||F\r')
    return {
        'sample.hl7': message.encode(),
        'correction.hl7': message.replace('|5.1|', '|5.3|')
                                 .replace('||||F', '||||C').encode(),
        'alternate.hl7': (
            b'MSH*$%!?*LAB*LOCAL*OBS*LOCAL*20261112**ORU$R01*m2*P*2.5\r'
            b'OBX*1*NM*X$Value$L*a*7*unit*****F\r'),
        'invalid.hl7': b'OBX|1|NM|X|a|5',
        # 自定义分隔符（字段 *、组件 $、重复 %、转义 !、子组件 ?）下的
        # 转义与重复值：!E! 还原为转义符本身，!R! 还原为重复分隔符（文本），
        # 两个 % 重复必须解释成两个值，不能挤成一个字符串。
        'alternate_escape.hl7': (
            'MSH*$%!?*LAB*REMOTE-LAB*OBS*LOCAL*202611130900**ORU$R01*m3*P*2.5\r'
            'PID*1**synthetic-7\r'
            'OBR*1*order-7**K$Potassium$L\r'
            'OBX*1*NM*K$Potassium$L*1*4%4.2*mmol/L*3.5-5.1****F\r'
            'OBX*2*ST*NOTE$Note$L*2*line!E!break%a!R!b******F\r').encode(),
        # 同一项目的两个子观察编号（OBX-4），以及对子观察 2 的精确撤销。
        'subids.hl7': (
            'MSH|^~\\&|LAB|LOCAL|OBS|LOCAL|202611131000||ORU^R01|m-4|P|2.5\r'
            'PID|1||synthetic-2\r'
            'OBR|1|order-2||CULT^Culture^L\r'
            'OBX|1|CE|CULT^Culture^L|1|ORG1^E.coli^L99||||||F\r'
            'OBX|2|NM|CULT^Culture^L|2|1000|CFU/mL|||||F\r').encode(),
        'subid_cancel.hl7': (
            'MSH|^~\\&|LAB|LOCAL|OBS|LOCAL|202611131005||ORU^R01|m-5|P|2.5\r'
            'PID|1||synthetic-2\r'
            'OBR|1|order-2||CULT^Culture^L\r'
            # 值(5)为空，6-10 空，状态 D 在 11
            'OBX|1|CE|CULT^Culture^L|2|||||||D\r').encode(),
        # 协议空值、空字段、非法 NM 与未知值类型：隔离但保留上下文。
        'edge_values.hl7': (
            'MSH|^~\\&|LAB|LOCAL|OBS|LOCAL|202611131100||ORU^R01|m-6|P|2.5\r'
            'PID|1||synthetic-3\r'
            'OBR|1|order-3||PANEL^Panel^L\r'
            'OBX|1|NM|GLU^Glucose^L|1|||||||F\r'
            'OBX|2|NM|GLU^Glucose^L|2|""||||||F\r'
            'OBX|3|NM|GLU^Glucose^L|3|oops||||||F\r'
            'OBX|4|ZZ|GLU^Glucose^L|4|mystery||||||F\r').encode(),
        # 不同申请单上复用相同段号/项目/患者标识，证明段号与患者号不能去重。
        'other_order.hl7': (
            'MSH|^~\\&|LAB|LOCAL|OBS|LOCAL|202611131200||ORU^R01|m-7|P|2.5\r'
            'PID|1||synthetic-1\r'
            'OBR|1|order-9||GLU^Glucose^L\r'
            'OBX|1|NM|GLU^Glucose^L|a|6.8|mmol/L|3.9-6.1||||F\r').encode(),
    }
