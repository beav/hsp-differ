import requests
import argparse
import json
from tqdm.auto import tqdm
from dictdiffer import diff
import uuid


def get_hsp(profile_id, args):
    response = requests.get(
        f"https://cloud.redhat.com/api/historical-system-profiles/v1/profiles/{profile_id}",
        auth=(args.api_username, args.api_password),
    )
    return response.json()["data"][0]["system_profile"]


def _is_uuid(input_string):
    try:
        uuid.UUID(input_string)
        return True
    except:
        return False


def clean_hsp(hsp):
    running_processes = {
        p for p in hsp["running_processes"] if not p.startswith("kworker")
    }
    hsp["running_processes"] = running_processes
    installed_products = {p.get("id") for p in hsp["installed_products"]}
    hsp["installed_products"] = installed_products
    del hsp["id"]
    del hsp["last_boot_time"]  # this toggles and is not usable currently
    return hsp


parser = argparse.ArgumentParser(description="view changes for an insights host")
parser.add_argument("inventory_id", help="inventory ID or display name")
parser.add_argument("api_username", help="cloud.redhat.com username")
parser.add_argument("api_password", help="cloud.redhat.com password")
parser.add_argument(
    "-a",
    "--api_hostname",
    default="cloud.redhat.com",
    help="API hostname to connect to",
)
parser.add_argument(
    "--disable-tls-validation",
    dest="tls_validation",
    action="store_false",
    help="disable TLS validation (only useful for testing)",
)
parser.set_defaults(tls_validation=True)

args = parser.parse_args()

inv_uuid = args.inventory_id
verify = args.tls_validation

if not _is_uuid(args.inventory_id):
    # assume we got a display name if we didn't get a uuid
    inv_record = requests.get(
        f"https://{args.api_hostname}/api/inventory/v1/hosts?display_name={inv_uuid}",
        auth=(args.api_username, args.api_password),
        verify=verify,
    ).json()
    inv_uuid = inv_record["results"][0]["id"]


host_data = requests.get(
    f"https://{args.api_hostname}/api/inventory/v1/hosts/{inv_uuid}",
    auth=(args.api_username, args.api_password),
    verify=verify,
).json()

display_name = host_data["results"][0]["display_name"]

tqdm.write(f"fetching historical profiles for {display_name}...")
response = requests.get(
    f"https://{args.api_hostname}/api/historical-system-profiles/v1/systems/{inv_uuid}",
    auth=(args.api_username, args.api_password),
    verify=verify,
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
            if d[1] not in ("captured_date",):
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
            f"changes from {hsps[i]['captured_date']} to {hsps[i-1]['captured_date']}:"
        )
        for c in report["changes"]:
            print("\tCHANGED:")
            print(f"\t\t{c[0]}:")
            print(f"\t\t\tFROM:\t{c[1][0]}")
            print(f"\t\t\tTO:\t{c[1][1]}")
        for a in report["added"]:
            print("\tADDED:")
            print(f"\t\t{a[0]}:")
            print(f"\t\t\t\t{a[1][0][1]}")
        for r in report["removed"]:
            print("\tREMOVED:")
            print(f"\t\t{r[0]}:")
            print(f"\t\t\t\t{r[1][0][1]}")
