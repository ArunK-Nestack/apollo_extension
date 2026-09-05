import pandas as pd
import sys, os
sys.path.append(os.getcwd())
from backend.api import get_connection

def main():
    batch_name = input("Enter the batch name to extract: ").strip()
    if not batch_name:
        print("Batch name cannot be empty.")
        return

    with get_connection() as conn:
        query = "SELECT * FROM apollo_saved_leads WHERE batch = %s"
        df = pd.read_sql(query, conn, params=(batch_name,))
        
    if df.empty:
        print(f"No leads found for batch: {batch_name}")
        return
        
    print(f"Found {len(df)} total leads for batch '{batch_name}'.")
    
    # Sort to prioritize "better" leads. 
    # We prioritize rows that have a linkedin_url or job_title.
    df['score'] = 0
    if 'job_title' in df.columns:
        df['score'] += df['job_title'].notna().astype(int)
    if 'linkedin_url' in df.columns:
        df['score'] += df['linkedin_url'].notna().astype(int)
        
    df = df.sort_values(by=['score', 'id'], ascending=[False, True])
    
    # Drop duplicates based on company_domain, keeping the first (which has the highest score)
    df_unique = df.drop_duplicates(subset=['company_domain'], keep='first')
    
    # Drop the temporary score column
    df_unique = df_unique.drop(columns=['score'])
    
    output_filename = f"{batch_name}_unique_leads.csv"
    df_unique.to_csv(output_filename, index=False)
    
    print(f"Extracted {len(df_unique)} unique leads (deduplicated by domain).")
    print(f"Saved to {output_filename}")

if __name__ == "__main__":
    main()
