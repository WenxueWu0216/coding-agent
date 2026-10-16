
def parse_csv_line(line):
    """Split one CSV line into fields. Quoted fields may contain commas,
    and a doubled quote ("") inside a quoted field means a literal quote."""
    fields, cur, in_q, i = [], [], False, 0
    while i < len(line):
        ch = line[i]
        if in_q:
            if ch == '"' and i + 1 < len(line) and line[i + 1] == '"':
                cur.append('"')
                i += 1
            elif ch == '"':
                in_q = False
            else:
                cur.append(ch)
        elif ch == '"':
            in_q = True
        elif ch == ",":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    fields.append("".join(cur))
    return fields
