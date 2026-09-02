from backend.api import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM apollo_saved_leads WHERE batch='vijay_raghavan_nestack_com-sep_new';")
        print('vijay_raghavan_nestack_com-sep_new:', cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM apollo_saved_leads WHERE batch='vijay_raghavan_nestack_com-sep';")
        print('vijay_raghavan_nestack_com-sep:', cur.fetchone()[0])
