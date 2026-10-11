"""Callsign prefix to country, ISO code and a rough locator.

This is the short version, not the real thing. Proper DXCC needs `cty.dat`,
which tracks every entity, every exception and every expedition callsign, and
changes several times a year. What is here covers the prefixes an operator
actually meets, well enough to put a flag beside a callsign and a pin roughly
in the right country.

Two things it is deliberately not:

* **Not DXCC entities.** Scotland, Wales and England are three DXCC entities
  and one flag, because the flag answers "where is this person" and DXCC
  answers "what counts as a separate country for awards". Alaska and Hawaii
  get the US flag for the same reason.
* **Not authoritative.** Everything from here is marked `approximate`, and a
  real lookup provider always wins.

ISO codes are ISO 3166-1 alpha-2, lower case, which is what the flag files
are named.
"""
from __future__ import annotations

# prefix -> (country, ISO 3166-1 alpha-2, rough locator)
# Longest prefix wins, so KH6 is matched before K.
PREFIXES: dict[str, tuple[str, str, str]] = {
    # North America
    "K": ("United States", "us", "EM48"), "W": ("United States", "us", "EM48"),
    "N": ("United States", "us", "EM48"), "AA": ("United States", "us", "EM48"),
    "AB": ("United States", "us", "EM48"), "AC": ("United States", "us", "EM48"),
    "AD": ("United States", "us", "EM48"), "AE": ("United States", "us", "EM48"),
    "AF": ("United States", "us", "EM48"), "AG": ("United States", "us", "EM48"),
    "AI": ("United States", "us", "EM48"), "AJ": ("United States", "us", "EM48"),
    "AK": ("United States", "us", "EM48"), "AL": ("Alaska", "us", "BP51"),
    "KL": ("Alaska", "us", "BP51"), "NL": ("Alaska", "us", "BP51"),
    "WL": ("Alaska", "us", "BP51"),
    "KH6": ("Hawaii", "us", "BL11"), "AH6": ("Hawaii", "us", "BL11"),
    "NH6": ("Hawaii", "us", "BL11"), "WH6": ("Hawaii", "us", "BL11"),
    "KP4": ("Puerto Rico", "pr", "FK68"), "NP4": ("Puerto Rico", "pr", "FK68"),
    "WP4": ("Puerto Rico", "pr", "FK68"),
    "KP2": ("US Virgin Islands", "vi", "FK77"),
    "VE": ("Canada", "ca", "FN25"), "VA": ("Canada", "ca", "FN25"),
    "VY": ("Canada", "ca", "FP53"), "VO": ("Canada", "ca", "GN37"),
    "CY": ("Canada", "ca", "GN03"),
    "XE": ("Mexico", "mx", "EK09"), "XF": ("Mexico", "mx", "EK09"),
    "CO": ("Cuba", "cu", "EL82"), "CM": ("Cuba", "cu", "EL82"),
    "HI": ("Dominican Republic", "do", "FK58"),
    "HH": ("Haiti", "ht", "FK38"), "6Y": ("Jamaica", "jm", "FK18"),
    "TI": ("Costa Rica", "cr", "EJ79"), "TG": ("Guatemala", "gt", "EK44"),
    "HP": ("Panama", "pa", "FJ08"), "YN": ("Nicaragua", "ni", "EK61"),
    "HR": ("Honduras", "hn", "EK54"), "YS": ("El Salvador", "sv", "EK43"),
    "V3": ("Belize", "bz", "EK56"), "8P": ("Barbados", "bb", "GK03"),
    "9Y": ("Trinidad and Tobago", "tt", "FK90"),
    "ZF": ("Cayman Islands", "ky", "EK99"),
    "C6": ("Bahamas", "bs", "FL15"), "VP9": ("Bermuda", "bm", "FM72"),
    # Both of these waited on a flag file rather than on knowing the answer:
    # the set had no `fk` or `cm`, and a code with no svg is a broken image
    # beside somebody's callsign. Pointing VP8 at `gb` in the meantime would
    # have been worse - the Falklands have their own flag and a wrong one is
    # read as fact.
    "VP8": ("Falkland Islands", "fk", "GD18"),
    "TJ": ("Cameroon", "cm", "JJ53"),
    "OX": ("Greenland", "gl", "GP60"),
    # South America
    "PY": ("Brazil", "br", "GG66"),
    # Brazil also has ZV-ZZ, which the table did not. ZW5K was on the
    # band when this was checked.
    "ZV": ("Brazil", "br", "GG66"), "ZW": ("Brazil", "br", "GG66"),
    "ZX": ("Brazil", "br", "GG66"), "ZY": ("Brazil", "br", "GG66"),
    "ZZ": ("Brazil", "br", "GG66"), "PP": ("Brazil", "br", "GG66"),
    "PT": ("Brazil", "br", "GG66"), "PU": ("Brazil", "br", "GG66"),
    "LU": ("Argentina", "ar", "GF05"), "CE": ("Chile", "cl", "FF46"),
    "CX": ("Uruguay", "uy", "GF15"), "CP": ("Bolivia", "bo", "FH72"),
    "OA": ("Peru", "pe", "FH17"), "HC": ("Ecuador", "ec", "FI07"),
    "HK": ("Colombia", "co", "FJ34"), "YV": ("Venezuela", "ve", "FK60"),
    "ZP": ("Paraguay", "py", "GG14"), "8R": ("Guyana", "gy", "GJ06"),
    "PZ": ("Suriname", "sr", "GJ25"),
    # Western Europe
    "G": ("England", "gb", "IO91"), "M": ("England", "gb", "IO91"),
    "2E": ("England", "gb", "IO91"),
    "GM": ("Scotland", "gb", "IO85"), "MM": ("Scotland", "gb", "IO85"),
    "GW": ("Wales", "gb", "IO81"), "MW": ("Wales", "gb", "IO81"),
    "GI": ("Northern Ireland", "gb", "IO74"), "MI": ("Northern Ireland", "gb", "IO74"),
    "GD": ("Isle of Man", "im", "IO74"), "GJ": ("Jersey", "je", "IN89"),
    "GU": ("Guernsey", "gg", "IN89"),
    "EI": ("Ireland", "ie", "IO63"), "EJ": ("Ireland", "ie", "IO63"),
    "F": ("France", "fr", "JN18"), "TM": ("France", "fr", "JN18"),
    "DL": ("Germany", "de", "JO62"), "DA": ("Germany", "de", "JO62"),
    "DB": ("Germany", "de", "JO62"), "DC": ("Germany", "de", "JO62"),
    "DD": ("Germany", "de", "JO62"), "DF": ("Germany", "de", "JO62"),
    "DG": ("Germany", "de", "JO62"), "DH": ("Germany", "de", "JO62"),
    "DJ": ("Germany", "de", "JO62"), "DK": ("Germany", "de", "JO62"),
    "DM": ("Germany", "de", "JO62"), "DO": ("Germany", "de", "JO62"),
    "PA": ("Netherlands", "nl", "JO22"), "PB": ("Netherlands", "nl", "JO22"),
    "PD": ("Netherlands", "nl", "JO22"), "PE": ("Netherlands", "nl", "JO22"),
    "PI": ("Netherlands", "nl", "JO22"),
    "ON": ("Belgium", "be", "JO20"), "OO": ("Belgium", "be", "JO20"),
    "LX": ("Luxembourg", "lu", "JN39"),
    "OZ": ("Denmark", "dk", "JO65"), "OU": ("Denmark", "dk", "JO65"),
    "SM": ("Sweden", "se", "JO89"), "SA": ("Sweden", "se", "JO89"),
    "SB": ("Sweden", "se", "JO89"), "SK": ("Sweden", "se", "JO89"),
    "LA": ("Norway", "no", "JO59"),
    # Svalbard and Jan Mayen: a separate DXCC entity that flies the
    # Norwegian flag, so the flag is Norway's and the grid is its own.
    "JW": ("Svalbard", "no", "JQ78"), "JX": ("Jan Mayen", "no", "IQ50"), "LB": ("Norway", "no", "JO59"),
    "LN": ("Norway", "no", "JO59"),
    "OH": ("Finland", "fi", "KP20"), "OF": ("Finland", "fi", "KP20"),
    "OG": ("Finland", "fi", "KP20"), "OH0": ("Aland Islands", "ax", "JP90"),
    "TF": ("Iceland", "is", "HP94"), "OY": ("Faroe Islands", "fo", "IP62"),
    "I": ("Italy", "it", "JN61"), "IK": ("Italy", "it", "JN61"),
    "IZ": ("Italy", "it", "JN61"), "IW": ("Italy", "it", "JN61"),
    "EA": ("Spain", "es", "IN80"), "EB": ("Spain", "es", "IN80"),
    "EC": ("Spain", "es", "IN80"), "ED": ("Spain", "es", "IN80"),
    "EA8": ("Canary Islands", "es", "IL18"), "EA6": ("Balearic Islands", "es", "JM19"),
    "CT": ("Portugal", "pt", "IM58"), "CR": ("Portugal", "pt", "IM58"),
    "CU": ("Azores", "pt", "HM68"), "CT3": ("Madeira", "pt", "IM12"),
    "CS": ("Portugal", "pt", "IM58"), "CS3": ("Madeira", "pt", "IM12"),
    "HB": ("Switzerland", "ch", "JN47"), "HB0": ("Liechtenstein", "li", "JN47"),
    "OE": ("Austria", "at", "JN88"),
    "SP": ("Poland", "pl", "KO02"), "SQ": ("Poland", "pl", "KO02"),
    "SN": ("Poland", "pl", "KO02"),
    "OK": ("Czechia", "cz", "JO70"), "OL": ("Czechia", "cz", "JO70"),
    "OM": ("Slovakia", "sk", "JN88"), "HA": ("Hungary", "hu", "JN97"),
    "HG": ("Hungary", "hu", "JN97"), "YO": ("Romania", "ro", "KN34"),
    "LZ": ("Bulgaria", "bg", "KN12"), "SV": ("Greece", "gr", "KM18"),
    "SW": ("Greece", "gr", "KM18"), "SY": ("Greece", "gr", "KM18"),
    "5B": ("Cyprus", "cy", "KM64"), "TA": ("Turkey", "tr", "KM69"),
    "S5": ("Slovenia", "si", "JN76"), "9A": ("Croatia", "hr", "JN85"),
    "E7": ("Bosnia and Herzegovina", "ba", "JN83"),
    "YU": ("Serbia", "rs", "KN04"), "YT": ("Serbia", "rs", "KN04"),
    "4O": ("Montenegro", "me", "JN92"), "Z3": ("North Macedonia", "mk", "KN01"),
    "ZA": ("Albania", "al", "JN91"), "T7": ("San Marino", "sm", "JN63"),
    "9H": ("Malta", "mt", "JM75"), "3A": ("Monaco", "mc", "JN33"),
    "C3": ("Andorra", "ad", "JN02"),
    # Eastern Europe and Asia
    "UA": ("Russia", "ru", "KO85"), "UB": ("Russia", "ru", "KO85"),
    "UC": ("Russia", "ru", "KO85"), "UD": ("Russia", "ru", "KO85"),
    "R": ("Russia", "ru", "KO85"), "UA9": ("Asiatic Russia", "ru", "NO26"),
    "UA0": ("Asiatic Russia", "ru", "OO12"),
    "UR": ("Ukraine", "ua", "KO50"), "US": ("Ukraine", "ua", "KO50"),
    "UT": ("Ukraine", "ua", "KO50"), "UY": ("Ukraine", "ua", "KO50"),
    "EU": ("Belarus", "by", "KO33"), "EW": ("Belarus", "by", "KO33"),
    "ES": ("Estonia", "ee", "KO29"), "YL": ("Latvia", "lv", "KO26"),
    "LY": ("Lithuania", "lt", "KO24"), "ER": ("Moldova", "md", "KN47"),
    "4L": ("Georgia", "ge", "LN21"), "EK": ("Armenia", "am", "LN10"),
    "4J": ("Azerbaijan", "az", "LN40"), "UN": ("Kazakhstan", "kz", "MN69"),
    "JA": ("Japan", "jp", "PM95"), "JE": ("Japan", "jp", "PM95"),
    "JF": ("Japan", "jp", "PM95"), "JG": ("Japan", "jp", "PM95"),
    "JH": ("Japan", "jp", "PM95"), "JI": ("Japan", "jp", "PM95"),
    "JJ": ("Japan", "jp", "PM95"), "JK": ("Japan", "jp", "PM95"),
    "JL": ("Japan", "jp", "PM95"), "JM": ("Japan", "jp", "PM95"),
    "JN": ("Japan", "jp", "PM95"), "JO": ("Japan", "jp", "PM95"),
    "JP": ("Japan", "jp", "PM95"), "JQ": ("Japan", "jp", "PM95"),
    "JR": ("Japan", "jp", "PM95"), "JS": ("Japan", "jp", "PM95"),
    # Japan is allocated 7J-7N and 8J-8N, not just the three that were
    # here. 7M4QZE/7 came off a live SOTA spot and flew no flag.
    "7J": ("Japan", "jp", "PM95"), "7K": ("Japan", "jp", "PM95"),
    "7L": ("Japan", "jp", "PM95"), "7M": ("Japan", "jp", "PM95"),
    "7N": ("Japan", "jp", "PM95"),
    "8J": ("Japan", "jp", "PM95"), "8K": ("Japan", "jp", "PM95"),
    "8L": ("Japan", "jp", "PM95"), "8M": ("Japan", "jp", "PM95"),
    "8N": ("Japan", "jp", "PM95"),
    "HL": ("South Korea", "kr", "PM37"), "DS": ("South Korea", "kr", "PM37"),
    "6K": ("South Korea", "kr", "PM37"), "P5": ("North Korea", "kp", "PN37"),
    "BY": ("China", "cn", "OM89"), "BA": ("China", "cn", "OM89"),
    "BD": ("China", "cn", "OM89"), "BG": ("China", "cn", "OM89"),
    "BH": ("China", "cn", "OM89"), "BI": ("China", "cn", "OM89"),
    "BV": ("Taiwan", "tw", "PL05"), "VR": ("Hong Kong", "hk", "OL72"),
    "XX9": ("Macao", "mo", "OL62"),
    "VU": ("India", "in", "MK88"), "AT": ("India", "in", "MK88"),
    "4S": ("Sri Lanka", "lk", "MJ96"), "S2": ("Bangladesh", "bd", "NL45"),
    "AP": ("Pakistan", "pk", "MM24"), "9N": ("Nepal", "np", "ML27"),
    "A5": ("Bhutan", "bt", "NL17"),
    "9M": ("Malaysia", "my", "OJ03"), "9V": ("Singapore", "sg", "OJ11"),
    "HS": ("Thailand", "th", "OK03"), "E2": ("Thailand", "th", "OK03"),
    "XU": ("Cambodia", "kh", "OK21"), "XW": ("Laos", "la", "OK32"),
    "3W": ("Vietnam", "vn", "OK30"), "XV": ("Vietnam", "vn", "OK30"),
    "YB": ("Indonesia", "id", "OI33"), "YC": ("Indonesia", "id", "OI33"),
    "YD": ("Indonesia", "id", "OI33"), "YE": ("Indonesia", "id", "OI33"),
    "DU": ("Philippines", "ph", "PK04"), "DV": ("Philippines", "ph", "PK04"),
    "DW": ("Philippines", "ph", "PK04"), "DX": ("Philippines", "ph", "PK04"),
    "XZ": ("Myanmar", "mm", "NK96"), "V8": ("Brunei", "bn", "OJ35"),
    "UK": ("Uzbekistan", "uz", "MN41"), "EY": ("Tajikistan", "tj", "MM58"),
    "EZ": ("Turkmenistan", "tm", "LN02"), "EX": ("Kyrgyzstan", "kg", "MN72"),
    "JT": ("Mongolia", "mn", "ON25"),
    # Middle East
    "4X": ("Israel", "il", "KM72"), "4Z": ("Israel", "il", "KM72"),
    "JY": ("Jordan", "jo", "KM71"), "OD": ("Lebanon", "lb", "KM73"),
    "YK": ("Syria", "sy", "KM83"), "YI": ("Iraq", "iq", "LM43"),
    "EP": ("Iran", "ir", "LL73"), "A4": ("Oman", "om", "LL93"),
    "A6": ("United Arab Emirates", "ae", "LL75"), "A7": ("Qatar", "qa", "LL55"),
    "A9": ("Bahrain", "bh", "LL56"), "9K": ("Kuwait", "kw", "LL49"),
    "HZ": ("Saudi Arabia", "sa", "LL34"), "7O": ("Yemen", "ye", "LK62"),
    # Africa
    "ZS": ("South Africa", "za", "KG33"), "ZR": ("South Africa", "za", "KG33"),
    "ZT": ("South Africa", "za", "KG33"),
    "SU": ("Egypt", "eg", "KM51"), "CN": ("Morocco", "ma", "IM63"),
    "7X": ("Algeria", "dz", "JM16"), "3V": ("Tunisia", "tn", "JM56"),
    "5A": ("Libya", "ly", "JM75"), "ST": ("Sudan", "sd", "KK55"),
    "ET": ("Ethiopia", "et", "KJ99"), "5Z": ("Kenya", "ke", "KI88"),
    "5H": ("Tanzania", "tz", "KI94"), "5X": ("Uganda", "ug", "KJ50"),
    "9J": ("Zambia", "zm", "KH45"), "Z2": ("Zimbabwe", "zw", "KH52"),
    "C9": ("Mozambique", "mz", "KH61"), "V5": ("Namibia", "na", "JG87"),
    "A2": ("Botswana", "bw", "KG27"), "7P": ("Lesotho", "ls", "KG30"),
    "3DA": ("Eswatini", "sz", "KG53"), "5R": ("Madagascar", "mg", "LH25"),
    "S7": ("Seychelles", "sc", "LI75"), "3B8": ("Mauritius", "mu", "LG89"),
    "FR": ("Reunion", "re", "LG78"), "D4": ("Cape Verde", "cv", "HK76"),
    "5N": ("Nigeria", "ng", "JJ26"), "9G": ("Ghana", "gh", "IJ95"),
    "TU": ("Ivory Coast", "ci", "IJ56"), "6W": ("Senegal", "sn", "IK14"),
    "TY": ("Benin", "bj", "JJ16"), "TZ": ("Mali", "ml", "IK52"),
    "TR": ("Gabon", "ga", "JJ40"), "TN": ("Congo", "cg", "JI75"),
    "9Q": ("DR Congo", "cd", "JI76"), "D2": ("Angola", "ao", "JI65"),
    # Oceania
    "VK": ("Australia", "au", "QF56"), "AX": ("Australia", "au", "QF56"),
    "VI": ("Australia", "au", "QF56"),
    "ZL": ("New Zealand", "nz", "RE68"), "ZM": ("New Zealand", "nz", "RE68"),
    "P2": ("Papua New Guinea", "pg", "QI20"), "YJ": ("Vanuatu", "vu", "RH42"),
    "3D2": ("Fiji", "fj", "RH91"), "5W": ("Samoa", "ws", "AH46"),
    "A3": ("Tonga", "to", "AG28"), "FO": ("French Polynesia", "pf", "BH52"),
    "FK": ("New Caledonia", "nc", "RG27"), "KH2": ("Guam", "gu", "QK23"),
    "V7": ("Marshall Islands", "mh", "RJ38"), "T8": ("Palau", "pw", "PJ77"),
    "V6": ("Micronesia", "fm", "QJ96"), "T2": ("Tuvalu", "tv", "RI58"),
    "C2": ("Nauru", "nr", "RI39"), "T3": ("Kiribati", "ki", "AI22"),

    # The rest of each country's ITU call-sign block. The table used
    # to hold whichever prefixes somebody had needed: Germany had
    # twelve of its eighteen letters, Sweden four of thirteen, and a
    # live listen turned up SZ1A, DP75, SE5E, DR4W, DE1L, 9W2E and
    # AO5C with no country between them. Every entry here reuses a
    # country already in the table, so none of them is a new guess.
    "3Z": ("Poland", "pl", "KO02"),
    "9W": ("Malaysia", "my", "OJ03"),
    "AM": ("Spain", "es", "IN80"),
    "AN": ("Spain", "es", "IN80"),
    "AO": ("Spain", "es", "IN80"),
    "BB": ("China", "cn", "OM89"),
    "BC": ("China", "cn", "OM89"),
    "BE": ("China", "cn", "OM89"),
    "BF": ("China", "cn", "OM89"),
    "BJ": ("China", "cn", "OM89"),
    "BK": ("China", "cn", "OM89"),
    "BL": ("China", "cn", "OM89"),
    "BM": ("Taiwan", "tw", "PL05"),
    "BN": ("Taiwan", "tw", "PL05"),
    "BO": ("Taiwan", "tw", "PL05"),
    "BP": ("Taiwan", "tw", "PL05"),
    "BQ": ("Taiwan", "tw", "PL05"),
    "BT": ("China", "cn", "OM89"),
    "BU": ("Taiwan", "tw", "PL05"),
    "BW": ("Taiwan", "tw", "PL05"),
    "BX": ("Taiwan", "tw", "PL05"),
    "BZ": ("China", "cn", "OM89"),
    "CL": ("Cuba", "cu", "EL82"),
    "CQ": ("Portugal", "pt", "IM58"),
    "DE": ("Germany", "de", "JO62"),
    "DI": ("Germany", "de", "JO62"),
    "DN": ("Germany", "de", "JO62"),
    "DP": ("Germany", "de", "JO62"),
    "DQ": ("Germany", "de", "JO62"),
    "DR": ("Germany", "de", "JO62"),
    "DY": ("Philippines", "ph", "PK04"),
    "DZ": ("Philippines", "ph", "PK04"),
    "EE": ("Spain", "es", "IN80"),
    "EF": ("Spain", "es", "IN80"),
    "EG": ("Spain", "es", "IN80"),
    "EH": ("Spain", "es", "IN80"),
    "EM": ("Ukraine", "ua", "KO50"),
    "EN": ("Ukraine", "ua", "KO50"),
    "EO": ("Ukraine", "ua", "KO50"),
    "EV": ("Belarus", "by", "KO33"),
    "HF": ("Poland", "pl", "KO02"),
    "JD": ("Japan", "jp", "PM95"),
    "LC": ("Norway", "no", "JO59"),
    "LD": ("Norway", "no", "JO59"),
    "LE": ("Norway", "no", "JO59"),
    "LF": ("Norway", "no", "JO59"),
    "LG": ("Norway", "no", "JO59"),
    "LH": ("Norway", "no", "JO59"),
    "LI": ("Norway", "no", "JO59"),
    "LJ": ("Norway", "no", "JO59"),
    "LK": ("Norway", "no", "JO59"),
    "LL": ("Norway", "no", "JO59"),
    "LM": ("Norway", "no", "JO59"),
    "OI": ("Finland", "fi", "KP20"),
    "OJ": ("Finland", "fi", "KP20"),
    "OP": ("Belgium", "be", "JO20"),
    "OQ": ("Belgium", "be", "JO20"),
    "OR": ("Belgium", "be", "JO20"),
    "OS": ("Belgium", "be", "JO20"),
    "OT": ("Belgium", "be", "JO20"),
    "OV": ("Denmark", "dk", "JO65"),
    "OW": ("Denmark", "dk", "JO65"),
    "PC": ("Netherlands", "nl", "JO22"),
    "PF": ("Netherlands", "nl", "JO22"),
    "PG": ("Netherlands", "nl", "JO22"),
    "PH": ("Netherlands", "nl", "JO22"),
    "PQ": ("Brazil", "br", "GG66"),
    "PR": ("Brazil", "br", "GG66"),
    "PS": ("Brazil", "br", "GG66"),
    "PV": ("Brazil", "br", "GG66"),
    "PW": ("Brazil", "br", "GG66"),
    "PX": ("Brazil", "br", "GG66"),
    "SC": ("Sweden", "se", "JO89"),
    "SD": ("Sweden", "se", "JO89"),
    "SE": ("Sweden", "se", "JO89"),
    "SF": ("Sweden", "se", "JO89"),
    "SG": ("Sweden", "se", "JO89"),
    "SH": ("Sweden", "se", "JO89"),
    "SI": ("Sweden", "se", "JO89"),
    "SJ": ("Sweden", "se", "JO89"),
    "SL": ("Sweden", "se", "JO89"),
    "SO": ("Poland", "pl", "KO02"),
    "SR": ("Poland", "pl", "KO02"),
    "SX": ("Greece", "gr", "KM18"),
    "SZ": ("Greece", "gr", "KM18"),
    "UE": ("Russia", "ru", "KO85"),
    "UF": ("Russia", "ru", "KO85"),
    "UG": ("Russia", "ru", "KO85"),
    "UH": ("Russia", "ru", "KO85"),
    "UI": ("Russia", "ru", "KO85"),
    "UU": ("Ukraine", "ua", "KO50"),
    "UV": ("Ukraine", "ua", "KO50"),
    "UW": ("Ukraine", "ua", "KO50"),
    "UX": ("Ukraine", "ua", "KO50"),
    "UZ": ("Ukraine", "ua", "KO50"),
    "XA": ("Mexico", "mx", "EK09"),
    "XB": ("Mexico", "mx", "EK09"),
    "XC": ("Mexico", "mx", "EK09"),
    "XD": ("Mexico", "mx", "EK09"),
    "XG": ("Mexico", "mx", "EK09"),
    "XH": ("Mexico", "mx", "EK09"),
    "XI": ("Mexico", "mx", "EK09"),
    "YF": ("Indonesia", "id", "OI33"),
    "YG": ("Indonesia", "id", "OI33"),
    "YH": ("Indonesia", "id", "OI33"),
    "ZU": ("South Africa", "za", "KG33"),

}

# Longest first, so KH6 beats K and UA9 beats UA.
_ORDERED = sorted(PREFIXES, key=len, reverse=True)


def strip_call(call: str) -> str:
    """The home callsign inside a portable one.

    G0ABC/P and VE7XYZ/M carry a suffix; W1AW/VE7 carries a *prefix* that
    changes the country, and that is the part to match. Longest wins, which
    is right either way: the operator is where the prefix says.
    """
    c = (call or "").strip().upper()
    if "/" not in c:
        return c
    parts = [p for p in c.split("/") if p]
    if not parts:
        return c
    # A short trailing part is a suffix (P, M, QRP, 7); anything else that
    # looks like a prefix wins over the base callsign.
    tail = parts[-1]
    if len(parts) >= 2 and tail not in ("P", "M", "MM", "AM", "QRP", "A") \
            and not tail.isdigit() and len(tail) <= 3 and _match(tail):
        return tail
    return parts[0]


def _match(token: str) -> tuple[str, str, str] | None:
    for p in _ORDERED:
        if token.startswith(p):
            return PREFIXES[p]
    return None


def for_call(call: str) -> dict:
    """Country, ISO code and a rough locator for a callsign.

    Always `approximate`: it is a prefix table, and the middle of a country
    is not where anybody lives.
    """
    token = strip_call(call)
    hit = _match(token)
    if not hit:
        return {"country": "", "iso": "", "locator": "", "approximate": True}
    country, iso, locator = hit
    return {"country": country, "iso": iso, "locator": locator,
            "approximate": True}


def iso_for(call: str) -> str:
    """Just the flag code, which is what the UI usually wants."""
    return for_call(call)["iso"]
