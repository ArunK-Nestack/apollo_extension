import requests
import json

payload = {
    "contacts": [
        {
            "key": "test_1",
            "name": "Jackie Marko",
            "job_title": "CEO",
            "company": "Whois Privacy Service",
            "company_domain": "whoisprivacyservice.org"
        }
    ]
}

response = requests.post("http://127.0.0.1:8000/match-apollo", json=payload)
print(json.dumps(response.json(), indent=2))
