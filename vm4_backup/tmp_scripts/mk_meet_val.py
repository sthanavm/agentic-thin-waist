s=open("/tmp/validate_post.py").read()
s=s.replace(chr(39)+chr(39)+chr(39),chr(39)+chr(39)+chr(39))
s=s.replace("    if j[\"timestamp\"] < BUILD: continue",
            "    if (j.get(\"config\") or {}).get(\"tag\") != \"meet_e2e\": continue")
s=s.replace("/tmp/post_rows.json","/tmp/meet_rows.json")
open("/tmp/validate_meet.py","w").write(s)
print("  retargeted to tag=meet_e2e")
