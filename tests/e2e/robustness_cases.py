"""Edge-case and randomly generated input files for the robustness tests."""

import json
import random


CASES = {
 "empty": b"",
 "header_only": b"a,b,c\n",
 "one_row": b"id,x\n1,5\n",
 "one_column": b"value\n1\n2\n3\n",
 "all_empty_values": b"a,b\n,\n,\n,\n",
 "all_identical_rows": b"a,b\n1,2\n1,2\n1,2\n1,2\n",
 "bom_semicolon": "﻿id;name;amount\n1;Ann;5,5\n2;Bob;6,1\n".encode("utf-8"),
 "tab": b"id\tname\n1\tA\n2\tB\n",
 "latin1": "id,name\n1,Jos\xe9\n2,Ren\xe9e\n".encode("latin-1"),
 "html_injection": b'id,"<script>alert(1)</script>",<b>x</b>\n1,"<img src=x onerror=alert(1)>",2\n2,"&amp;",3\n',
 "ragged": b"a,b\n1,2,3\n4,5\n6\n",
 "quoted_newlines": b'id,notes\n1,"line one\nline two"\n2,"x"\n',
 "numeric_only": ("x,y\n" + "".join(f"{i},{i*2.5}\n" for i in range(200))).encode(),
 "dates_mixed": b"id,d\n1,2024-01-05\n2,05/01/2024\n3,01/05/2024\n4,2099-01-01\n5,not a date\n6,\n",
 "sentinels_everywhere": ("id,a,b,c\n" + "".join(f"{i},{['-99','9999','N/A','Refused','5'][i%5]},{i},{['x','y','unknown'][i%3]}\n" for i in range(60))).encode(),
 "pii": b"id,email,ssn,phone,notes\n1,a@b.com,123-45-6789,555-123-4567,call 555-222-3333\n2,c@d.com,987-65-4321,555-987-6543,ok\n",
 "inf_nan": b"id,v\n1,nan\n2,inf\n3,-inf\n4,1e309\n5,3\n6,4\n",
 "huge_numbers": b"id,v\n1,1e308\n2,-1e308\n3,0\n4,123456789012345678901234567890\n",
 "weird_headers": b' id ,"a,b","",x\n1,2,3,4\n'.replace(b'""', b'"empty"'),
 "unicode_heavy": "id,name,city\n1,李雷,北京\n2,Zoë,Zürich\n3,😀,🏙️\n4,JosÃ©,SÃ£o Paulo\n".encode("utf-8"),
 "invisible": "id,name\n1,An​n\n2,Bob\xa0\n3,​\n".encode("utf-8"),
 "whitespace_only_cells": b"id,a\n1,   \n2,\t\n3,x\n",
 "long_text": ("id,t\n" + "".join(f'{i},"{"word " * 2000}"\n' for i in range(5))).encode(),
 "wide": (",".join(f"c{i}" for i in range(150)) + "\n" + "\n".join(",".join(str((r*i)%7) for i in range(150)) for r in range(30)) + "\n").encode(),
 "units_and_money": ("id,weight_kg,income,zip\n" + "".join(f"{i},{70 if i%10 else 154.3},{'$50,000' if i%7==0 else 50000+i},{8001 if i%4==0 else 80302}\n" for i in range(80))).encode(),
 "age_dob_visit": ("id,age,birth_date,visit_date,sex,pregnant,total_income,wage_income,other_income\n" + "".join(f"{i},{30+i%40},{1990-i%40}-03-04,2024-01-10,{'MF'[i%2]},{'Yes' if i%2 else ''},{100+i},{60+i},40\n" for i in range(60))).encode(),
 "duplicate_headers": b"a,a,b\n1,2,3\n",
 "json_records": json.dumps([{"id": i, "v": i * 1.5, "s": "x"} for i in range(20)]).encode(),
 "json_nested": json.dumps([{"id": 1, "o": {"x": 1}, "l": [1, 2]}]).encode(),
 "json_not_list": json.dumps({"a": 1}).encode(),
 "binary_garbage": bytes(range(256)) * 4,
 "all_null_column": ("id,empty,v\n" + "".join(f"{i},,{i}\n" for i in range(50))).encode(),
 "constant_column": ("id,k,v\n" + "".join(f"{i},same,{i}\n" for i in range(50))).encode(),
 "only_id_column": ("id\n" + "".join(f"P{i}\n" for i in range(50))).encode(),
 "crlf": b"id,x\r\n1,a\r\n2,b\r\n",
 "code_spelling_variants": ("id,status,smoker\n" + "".join(f"{i},{['N/A','n/a',' N/A','Unknown','unknown','Active','active'][i%7]},{['Never','never','NEVER','-99','Yes'][i%5]}\n" for i in range(70))).encode(),
 "trailing_footer": b"County,Deaths\nA,10\nB,20\n\"Notes:\"\n\"Query Parameters:\"\n",
}

def random_messy(seed: int) -> bytes:
    rng = random.Random(seed)
    cols = rng.sample(["id","name","age","sex","weight","height","income","state","zip","visit_date","birth_date","notes","status","score","email","fips","total_cost","part_cost","other_cost"], rng.randint(2, 10))
    if "id" not in cols: cols.insert(0, "id")
    pool = ["", " ", "9999", "-99", "N/A", "Unknown", "  x  ", "JosÃ©", "a​b", "$1,200", "12k", "1e3", "nan", "2024-02-30", "13/13/2020", "02/03/2021", "2099-12-31", "0", "-5", "1000000", "Yes", "no", "M", "f", "male", "Female", "<b>", "=SUM(A1)", "\"q\"", "😀"]
    lines = [",".join(cols)]
    n = rng.randint(0, 120)
    for i in range(n):
        row = []
        for c in cols:
            if c == "id": v = str(i if rng.random() > 0.05 else max(0, i-1))
            elif rng.random() < 0.3: v = rng.choice(pool)
            elif c in ("age","score"): v = str(rng.randint(0, 99))
            elif c in ("weight","height","income","total_cost","part_cost","other_cost"): v = f"{rng.uniform(1, 300):.1f}"
            elif c.endswith("date"): v = f"20{rng.randint(10,24)}-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}"
            elif c in ("zip","fips"): v = str(rng.randint(1000, 99999))
            else: v = rng.choice(["a","b","c","A","B"])
            if "," in v or '"' in v: v = '"' + v.replace('"','""') + '"'
            row.append(v)
        lines.append(",".join(row))
        if rng.random() < 0.05: lines.append(lines[-1])
    return ("\n".join(lines) + "\n").encode("utf-8")
