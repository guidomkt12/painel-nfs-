import os, json, subprocess, sys
from pathlib import Path
if __name__=="__main__":
    print("Painelzinho da Keile — demonstração local, sem dados reais")
    a=input("E-mail do primeiro usuário: ").strip().lower()
    b=input("E-mail do segundo usuário: ").strip().lower()
    if not a or not b or a==b: raise SystemExit("Informe dois e-mails distintos.")
    env={**os.environ,"DEMO_MODE":"1","PANEL_USERS_JSON":json.dumps({a:"123456",b:"123456"}),"PORT":"8080"}
    print("Abra http://127.0.0.1:8080 — senha 123456")
    subprocess.run([sys.executable,str(Path(__file__).with_name("server.py"))],env=env,check=True)
