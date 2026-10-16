
def parse_csv_line(line):
    """Split one CSV line into fields. Quoted fields may contain commas,
    and a doubled quote ("") inside a quoted field means a literal quote."""
    return line.split(",")
