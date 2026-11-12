def inspect_bytes(data):
    text = data.decode('utf8')
    if not text.startswith('MSH') or len(text) < 8:
        raise ValueError('不是 HL7 MSH 起始报文')
    separator = text[3]
    result = []
    for index, line in enumerate(text.replace('\n', '').split('\r')):
        if not line:
            continue
        fields = line.split(separator)
        if len(fields[0]) != 3:
            raise ValueError('段标识必须是三字符')
        result.append({'index': index, 'segment': fields[0], 'fields': fields[1:]})
    return result
