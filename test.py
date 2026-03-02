import requests
from requests.auth import HTTPBasicAuth
import json
import os

token = os.getenv("JIRA_API_TOKEN", "")
auth = HTTPBasicAuth("U202116508@upc.edu.pe", token)

url = "https://saelcc03-agents-league.atlassian.net/rest/api/3/search/jql"
headers = {
    "Accept": "application/json"
}

params = {
  "jql": "project = SCC",
  "fields": "summary,description,issuetype,status,assignee,priority,updated,customfield_10020,customfield_10016"
}

response = requests.get(url, headers=headers, params=params, auth=auth)

print("Status:", response.status_code)
print("Content-Type:", response.headers.get("content-type"))

print(json.dumps(response.json(), indent=2))

import requests
from requests.auth import HTTPBasicAuth
import json

url = "https://saelcc03-agents-league.atlassian.net/rest/api/3/project/recent"

auth = HTTPBasicAuth("u202116508@upc.edu.pe", token)

headers = {
  "Accept": "application/json"
}

response = requests.request(
   "GET",
   url,
   headers=headers,
   auth=auth
)

# print(json.dumps(json.loads(response.text), sort_keys=True, indent=4, separators=(",", ": ")))



# # Fields___________________________________
# import requests
# from requests.auth import HTTPBasicAuth
# import json

# url = "https://saelcc03-agents-league.atlassian.net/rest/api/3/field"


# response = requests.request(
#    "GET",
#    url,
#    headers=headers,
#    auth=auth
# )

# print(json.dumps(json.loads(response.text), sort_keys=True, indent=4, separators=(",", ": ")))






# # permission
# baseurl = "saelcc03-agents-league.atlassian.net"
# projectKey = "SCC"
# permission = "PROJECT_ADMIN"
# url = f"http://{baseurl}/rest/api/latest/projects/{projectKey}/permissions/{permission}/all"

# response = requests.request(
#    "POST",
#    url
# )

# print(response.text)