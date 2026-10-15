
def parse_date(s):
    year, month, day = s.split("-")
    return (int(year), int(month), int(day))
