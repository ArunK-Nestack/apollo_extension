import sys
import os

sys.path.append(os.getcwd())
from backend.api import get_connection

batch = "vijay_nestacktech_com-sep-new"

def run_audit():
    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Total leads in target batch
            cur.execute("""
                SELECT id, name, company, company_domain, website_link, linkedin_url 
                FROM apollo_saved_leads 
                WHERE batch = %s;
            """, (batch,))
            target_leads = cur.fetchall()
            
            # 2. Other batches in apollo_saved_leads
            cur.execute("""
                SELECT DISTINCT company_domain 
                FROM apollo_saved_leads 
                WHERE batch != %s AND company_domain IS NOT NULL AND company_domain != '';
            """, (batch,))
            other_batch_domains = set(r[0].lower().strip() for r in cur.fetchall() if r[0])
            
            # 3. Check domain overlaps with emails table
            target_domains = set(r[3].lower().strip() for r in target_leads if r[3])
            
            print(f"=== BATCH AUDIT: {batch} ===")
            print(f"Total leads in batch: {len(target_leads)}")
            print(f"Total distinct domains in batch: {len(target_domains)}")
            print(f"Distinct domains in OTHER batches (apollo_saved_leads): {len(other_batch_domains)}")
            
            # Check overlaps with other batches
            overlap_other_batches = target_domains.intersection(other_batch_domains)
            print(f"\n1. Overlap with OTHER batches in apollo_saved_leads: {len(overlap_other_batches)} domains")
            if overlap_other_batches:
                print("   Examples:", list(overlap_other_batches)[:10])
                
            # Check overlaps with emails table
            emails_overlap = set()
            domain_list = list(target_domains)
            chunk_size = 1000
            for i in range(0, len(domain_list), chunk_size):
                chunk = domain_list[i:i+chunk_size]
                placeholders = ",".join(["%s"] * len(chunk))
                cur.execute(f"SELECT DISTINCT domain FROM emails WHERE domain IN ({placeholders});", chunk)
                for r in cur.fetchall():
                    emails_overlap.add(r[0].lower().strip())
                    
            print(f"2. Overlap with CRM `emails` table: {len(emails_overlap)} domains")
            if emails_overlap:
                print("   Examples:", list(emails_overlap)[:10])
                
            # Combined exclusions
            all_excluded_domains = emails_overlap.union(overlap_other_batches)
            print(f"\n3. Total Unique Excluded Domains (emails table + other batches): {len(all_excluded_domains)}")
            
            # Net-new unique domains in target batch
            clean_domains = target_domains - all_excluded_domains
            print(f"\n4. TRUE Net-New Unique Domains: {len(clean_domains)}")
            
            # Check lead count matching clean domains
            clean_leads = [lead for lead in target_leads if lead[3] and lead[3].lower().strip() in clean_domains]
            print(f"5. Total Leads with Clean Unique Domains: {len(clean_leads)}")

if __name__ == "__main__":
    run_audit()
