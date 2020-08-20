import argparse
from http import HTTPStatus
import json
import uuid
import requests
import sys
from difflib import unified_diff

from dictdiffer import diff
from tqdm.auto import tqdm

get_host_by_name_url = "https://%s/api/inventory/v1/hosts?display_name=%s"
get_host_url = "https://%s/api/inventory/v1/hosts/%s"
get_profile_url = "https://%s/api/historical-system-profiles/v1/profiles/%s"
get_profile_list_url = "https://%s/api/historical-system-profiles/v1/systems/%s"


class SetEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, set):
            return sorted(list(obj))
        if isinstance(obj, list):
            return sorted(obj)
        return json.JSONEncoder.default(self, obj)


def _make_request(url, username, password, ssl_verify):
    response = requests.get(
        url, auth=(args.api_username, args.api_password), verify=ssl_verify
    )
    if response.status_code != HTTPStatus.OK:
        raise ValueError("bad response from server: %s" % response.status_code)
    result = response.json()
    if "data" in result and len(result["data"]) == 0:
        raise RuntimeError("no results found for request")
    elif "results" in result and len(result["results"]) == 0:
        raise RuntimeError("no results found for request")
    return result


def _fetch_comparison(hsps):
    n = 0
    while n < len(hsps) - 1:
        yield diff(hsps[n], hsps[n + 1])
        n = n + 1


def _fetch_unified_comparison(hsps):
    n = 0
    while n < len(hsps) - 1:
        old = json.dumps(hsps[n], cls=SetEncoder, sort_keys=True, indent=4)
        new = json.dumps(hsps[n + 1], cls=SetEncoder, sort_keys=True, indent=4)
        yield unified_diff(
            old.split("\n"),
            new.split("\n"),
            fromfile=hsps[n]["captured_date"],
            tofile=hsps[n + 1]["captured_date"],
        )
        n = n + 1


def get_hsp(profile_id, args):
    result = _make_request(
        get_profile_url % (args.api_hostname, profile_id),
        args.api_username,
        args.api_password,
        args.ssl_verify,
    )
    return result["data"][0]["system_profile"]


def _is_uuid(input_string):
    try:
        uuid.UUID(input_string)
        return True
    except:
        return False

        {
            "base_url": "https://repo.skype.com/rpm/stable/",
            "enabled": true,
            "gpgcheck": true,
            "id": "skype-stable",
            "name": "skype (stable)",
        },


def _parse_yum_repos(repos):
    parsed_repos = set()
    repo_template = "[%s] %s enabled: %s gpgcheck: %s"
    for repo in repos:
        parsed_repos.add(
            repo_template
            % (
                repo.get("name"),
                repo.get("base_url"),
                repo.get("enabled"),
                repo.get("gpgcheck"),
            )
        )
    return parsed_repos


def _parse_network_interfaces(interfaces):
    parsed_interfaces = set()
    interface_template_v4_addrs = "[%s] ipv4 addresses: %s"
    interface_template_v6_addrs = "[%s] ipv6 addresses: %s"
    interface_template = "[%s] type: %s state: %s mac_address: %s mtu: %s"
    for iface in interfaces:
        parsed_interfaces.add(
            interface_template
            % (
                iface.get("name"),
                iface.get("type"),
                iface.get("state"),
                iface.get("mac_address"),
                iface.get("mtu"),
            )
        )
        parsed_interfaces.add(
            interface_template_v4_addrs
            % (iface.get("name"), ", ".join(iface.get("ipv4_addresses", [])),)
        )
        parsed_interfaces.add(
            interface_template_v6_addrs
            % (iface.get("name"), ", ".join(iface.get("ipv6_addresses", [])),)
        )
    return parsed_interfaces


def _parse_dnf_modules(modules):
    parsed_modules = set()
    module_template = "%s %s"
    for module in modules:
        parsed_modules.add(module_template % (module["name"], module["stream"]))
    return parsed_modules


def clean_hsp(hsp):
    running_processes = {
        p for p in hsp["running_processes"] if not p.startswith("kworker")
    }
    hsp["running_processes"] = running_processes
    hsp["installed_packages"] = {p for p in hsp["installed_packages"]}
    installed_products = {p.get("id") for p in hsp["installed_products"]}
    hsp["installed_products"] = installed_products
    hsp["kernel_modules"] = {k for k in hsp["kernel_modules"]}
    hsp["installed_services"] = {s for s in hsp["installed_services"]}
    hsp["enabled_services"] = {s for s in hsp["enabled_services"]}
    hsp["dnf_modules"] = _parse_dnf_modules(hsp["dnf_modules"])
    hsp["network_interfaces"] = _parse_network_interfaces(hsp["network_interfaces"])
    hsp["yum_repos"] = _parse_yum_repos(hsp["yum_repos"])
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
    "--disable-ssl-verify",
    dest="ssl_verify",
    action="store_false",
    help="disable SSL hostname verification (only useful for testing)",
)
parser.set_defaults(tls_validation=True)

parser.add_argument(
    "--diff-view",
    dest="diff_view",
    action="store_true",
    help="show data in diff view instead of as report",
)
parser.set_defaults(diff_view=False)

args = parser.parse_args()

inv_uuid = args.inventory_id
verify = args.tls_validation


if not _is_uuid(args.inventory_id):
    # assume we got a display name if we didn't get a uuid
    inv_record = _make_request(
        get_host_by_name_url % (args.api_hostname, args.inventory_id),
        args.api_username,
        args.api_password,
        args.ssl_verify,
    )
    inv_uuid = inv_record["results"][0]["id"]


host_data = _make_request(
    get_host_url % (args.api_hostname, inv_uuid),
    args.api_username,
    args.api_password,
    args.ssl_verify,
)

display_name = host_data["results"][0]["display_name"]

tqdm.write(f"fetching historical profiles for {display_name}...")
changes = _make_request(
    get_profile_list_url % (args.api_hostname, inv_uuid),
    args.api_username,
    args.api_password,
    args.ssl_verify,
)
profiles = changes["data"][0]["profiles"]


hsps = []
for profile in tqdm(profiles, unit="profile"):
    raw_hsp = get_hsp(profile["id"], args)
    hsps.append(clean_hsp(raw_hsp))

sorted_hsps = sorted(hsps, key=lambda hsp: hsp.get("captured_date"))

if args.diff_view:
    for unified in _fetch_unified_comparison(sorted_hsps):
        for comparison in unified:
            print(comparison)
    sys.exit(0)

# TODO:  refactor and put "diff_view" check in an if/else; get rid of exit(0)
print(
    f"Change report for {display_name} from {sorted_hsps[0]['captured_date']} to {sorted_hsps[-1]['captured_date']}\n\n"
)

for comparison in _fetch_comparison(sorted_hsps):
    newline = "\n\t\t\t"
    report = {"changes": [], "added": [], "removed": []}
    for d in comparison:
        if d[0] == "change":
            report["changes"].append(d[1:])
        elif d[0] == "add":
            report["added"].append(d[1:])
        elif d[0] == "remove":
            report["removed"].append(d[1:])

    if len(report["changes"]) + len(report["added"]) + len(report["removed"]) == 0:
        continue
    elif len(report["changes"]) == 1 and report["changes"][0][0] == "captured_date":
        print(
            f"no detected changes from {report['changes'][0][1][0]} to {report['changes'][0][1][1]}"
        )
    else:
        for change in report["changes"]:
            if change[0] == "captured_date":
                print(f"changes from {change[1][0]} to {change[1][1]}")
        for c in report["changes"]:
            if c[0] != "captured_date":
                print("\tCHANGED:")
                print(f"\t\t{c[0]}:")
                print(f"\t\t\tFROM:\t{c[1][0]}")
                print(f"\t\t\tTO:\t{c[1][1]}")
        for a in report["added"]:
            print("\tADDED:")
            print(f"\t\t{a[0]}:")
            print(f"\t\t\t{newline.join(sorted(a[1][0][1]))}")
        for r in report["removed"]:
            print("\tREMOVED:")
            print(f"\t\t{r[0]}:")
            print(f"\t\t\t{newline.join(sorted(r[1][0][1]))}")
