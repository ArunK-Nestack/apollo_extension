from backend.api import check_person_and_domains_in_crm_batch, get_connection
import pymysql

class DummyContact:
    def __init__(self, key, name):
        self.key = key
        self.name = name

contacts = [DummyContact("test_1", "Jackie Marko")]
contact_primary_domain = {"test_1": "whoisprivacyservice.org"}

with get_connection() as conn:
    results = check_person_and_domains_in_crm_batch(contacts, contact_primary_domain, connection=conn)
    print("Results:", results)
