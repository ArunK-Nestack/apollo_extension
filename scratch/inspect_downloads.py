import pandas as pd
import glob
import os

files = [
    'abel.abraham@nestacktechnologies.com(10 Aug - 10 Sep)_OK_ONLY_MILLIONVERIFIER.COM.csv',
    'abel.abraham@nestacktechnologies.com(10 Aug - 10 Sep).csv',
    'Untitled spreadsheet - Sheet1 (33).csv',
    'apollo-contacts-export (23).csv',
    'apollo-contacts-export (24).csv',
    'apollo-contacts-export (25).csv'
]

for name in files:
    p = os.path.join(r'C:\Users\test\Downloads', name)
    if os.path.exists(p):
        df = pd.read_csv(p, low_memory=False)
        print(f"File: {name}")
        print(f"  Rows: {len(df)}, Cols: {len(df.columns)}")
        for col in ['Email', 'email', 'Contact : Emails', 'Website', 'Company Domain']:
            if col in df.columns:
                print(f"  {col}: {df[col].dropna().nunique()} unique (total non-null: {df[col].dropna().count()})")
        print()
