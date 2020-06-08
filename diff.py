import requests
import argparse
import json
from tqdm.auto import tqdm
from dictdiffer import diff


def get_hsp(profile_id, args):
    response = requests.get(
        f"https://cloud.redhat.com/api/historical-system-profiles/v1/profiles/{profile_id}",
        auth=(args.api_username, args.api_password),
    )
    return response.json()["data"][0]["system_profile"]


def clean_hsp(hsp):
    running_processes = {
        p for p in hsp["running_processes"] if not p.startswith("kworker")
    }
    hsp["running_processes"] = running_processes
    installed_products = {p.get("id") for p in hsp["installed_products"]}
    hsp["installed_products"] = installed_products
    del hsp["id"]
    return hsp


parser = argparse.ArgumentParser(description="view changes for an insights host")
parser.add_argument("inventory_id")
parser.add_argument("api_username")
parser.add_argument("api_password")

args = parser.parse_args()

inv_uuid = args.inventory_id

tqdm.write("fetching historical profiles...")
response = requests.get(
    f"https://cloud.redhat.com/api/historical-system-profiles/v1/systems/{inv_uuid}",
    auth=(args.api_username, args.api_password),
)

changes = response.json()
profiles = changes["data"][0]["profiles"]


hsps = []
for profile in tqdm(profiles, unit="profile"):
    raw_hsp = get_hsp(profile["id"], args)
    hsps.append(clean_hsp(raw_hsp))


for i in reversed(range(1, len(hsps))):
    report = {"changes": [], "added": [], "removed": []}
    hspdiff = diff(hsps[i], hsps[i - 1])
    for d in hspdiff:
        if d[0] == "change":
            report["changes"].append(d[1:])
        elif d[0] == "add":
            report["added"].append(d[1:])
        elif d[0] == "remove":
            report["removed"].append(d[1:])

    if len(report["changes"]) + len(report["added"]) + len(report["removed"]) == 0:
        # no change, keep on truckin
        continue
    else:
        print(
            f"changes from {hsps[i-1]['captured_date']} to {hsps[i]['captured_date']}:"
        )
        for c in report["changes"]:
            print("\tCHANGED:")
            print(f"\t\t{c[0]}:")
            print(f"\t\t\tFROM:\t{c[1][0]}")
            print(f"\t\t\tTO:\t{c[1][1]}")
        for a in report["added"]:
            print("\tADDED:")
            print(f"\t\t{a[0]}:")
            print(f"\t\t\t\t{a[1]}")
        for r in report["removed"]:
            print("\tREMOVED:")
            print(f"\t\t{r[0]}:")
            print(f"\t\t\t\t{r[1]}")
