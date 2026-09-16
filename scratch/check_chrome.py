import subprocess

try:
    cmd = 'powershell -NoProfile -Command "Get-Process chrome | Select-Object -First 5 -ExpandProperty Path"'
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print("Chrome path:", res.stdout)
except Exception as e:
    print("Error:", e)
