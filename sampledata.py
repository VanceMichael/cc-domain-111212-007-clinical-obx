def samples():
    message = 'MSH|^~\\&|LAB|LOCAL|OBS|LOCAL|202611120900||ORU^R01|m-1|P|2.5\rPID|1||synthetic-1\rOBR|1|order-1||GLU^Glucose^L\rOBX|1|NM|GLU^Glucose^L|a|5.1|mmol/L|3.9-6.1||||F\rOBX|2|ST|NOTE^Note^L|b|A\\F\\B~second||||||F\r'
    return {'sample.hl7': message.encode(),
            'correction.hl7': message.replace('|5.1|', '|5.3|').replace('||||F', '||||C').encode(),
            'alternate.hl7': b'MSH*$%!?*LAB*LOCAL*OBS*LOCAL*20261112**ORU$R01*m2*P*2.5\rOBX*1*NM*X$Value$L*a*7*unit*****F\r',
            'invalid.hl7': b'OBX|1|NM|X|a|5'}
