import subprocess
import sys

# Account 6, Search 1, No filter edit (n), No list exclusion (n), No save (n), batch name, then 'done'
simulated_input = "6\n1\nn\nn\nn\nMARCH_RECRUITING_BATCH_01\ndone\n"



p = subprocess.Popen(
    [sys.executable, "scripts/apollo_search_direct.py"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

stdout, stderr = p.communicate(input=simulated_input, timeout=60)
print("Return code:", p.returncode)
print("STDOUT FULL:\n")
print(stdout)

if stderr:
    print("STDERR:\n", stderr)
