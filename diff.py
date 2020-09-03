import re
import argparse
from http import HTTPStatus
import json
import uuid
import requests
import sys
from difflib import unified_diff

import dateparser
import datetime
from dictdiffer import diff
from tqdm.auto import tqdm

from insights.parsers.installed_rpms import InstalledRpm

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
        raise RuntimeError("bad response from server: %s" % response.status_code)
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
    parsed_interfaces = {}
    for iface in interfaces:
        parsed_interfaces[iface.get("name")] = {
            "ipv4 addresses": iface.get("ipv4_addresses", []),
            "ipv6 addresses": iface.get("ipv6_addresses", []),
            "type": iface.get("type"),
            "state": iface.get("state"),
            "mac_address": iface.get("mac_address"),
            "mtu": iface.get("mtu"),
        }
    return parsed_interfaces


def _parse_dnf_modules(modules):
    parsed_modules = set()
    module_template = "%s %s"
    for module in modules:
        parsed_modules.add(module_template % (module["name"], module["stream"]))
    return parsed_modules


def get_name_vra_from_string(rpm_string, part):
    """
    small helper to pull name + version/release/arch from string
    This supports two styles: ENVRA and NEVRA. The latter is preferred.
    """
    try:
        if re.match("^[0-9]+:", rpm_string):
            _, remainder = rpm_string.split(":", maxsplit=1)
            rpm = InstalledRpm.from_package(remainder)
        else:
            rpm = InstalledRpm.from_package(rpm_string)
    except TypeError:
        raise UnparsableNEVRAError("unable to parse %s into nevra" % rpm_string)

    vra = rpm.version if rpm.version else ""
    if rpm.release:
        vra = vra + "-" + rpm.release
    if rpm.arch:
        vra = vra + "." + rpm.arch

    if part == "name":
        return rpm.name
    else:
        return vra


def clean_hsp(hsp):
    running_processes = {
        p for p in hsp["running_processes"] if not p.startswith("kworker")
    }
    hsp["running_processes"] = running_processes
    hsp["installed_packages"] = {
        get_name_vra_from_string(p, "name"): get_name_vra_from_string(p, "vra")
        for p in hsp["installed_packages"]
    }
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

parser.add_argument(
    "--from_date",
    help="provide start of date range in format '2020-08-19', 'yesterday', 'AUG 31', etc, must also provide --to_date",
)
parser.add_argument(
    "--to_date",
    help="provide end of date range in format '2020-08-19', 'yesterday', 'AUG 31', etc, must also provide --from_date",
)

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

if args.from_date and args.to_date:
    from_date = dateparser.parse(
        args.from_date, settings={"RETURN_AS_TIMEZONE_AWARE": True}
    )
    to_date = dateparser.parse(
        args.to_date, settings={"RETURN_AS_TIMEZONE_AWARE": True}
    )
    ranged_sorted_hsps = []

    for hsp in sorted_hsps:
        captured = dateparser.parse(hsp["captured_date"])
        if from_date < captured < to_date + datetime.timedelta(days=1):
            ranged_sorted_hsps.append(hsp)
    sorted_hsps = ranged_sorted_hsps
    print(
            f"Change report for {display_name} from {from_date.strftime('%d %b %Y, %H:%M %Z')} to {to_date.strftime('%d %b %Y, %H:%M %Z')}\n\n"
    ) # note no slice to leave only UTC in this case due to timedelta use above
    if not sorted_hsps:
        print("No hsps within this date range.")
        sys.exit(0)

if args.diff_view:
    for unified in _fetch_unified_comparison(sorted_hsps):
        for comparison in unified:
            print(comparison)
    sys.exit(0)

# TODO:  refactor and put "diff_view" check in an if/else; get rid of exit(0)
if not args.from_date and not args.to_date:
    captured_from = dateparser.parse(sorted_hsps[0]['captured_date'])
    captured_to = dateparser.parse(sorted_hsps[-1]['captured_date'])
    print(
        f"Change report for {display_name} from {captured_from.strftime('%d %b %Y, %H:%M %Z')[:-7]} to {captured_to.strftime('%d %b %Y, %H:%M %Z')[:-7]}\n\n"
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
        no_change_from = dateparser.parse(report['changes'][0][1][0])
        no_change_to = dateparser.parse(report['changes'][0][1][1])
        print(
            f"changes from {no_change_from.strftime('%d %b %Y, %H:%M %Z')[:-7]} to {no_change_to.strftime('%d %b %Y, %H:%M %Z')[:-7]}\n\tNO CHANGE"
        )
    else:
        for change in report["changes"]:
            if change[0] == "captured_date":
                change_from = dateparser.parse(change[1][0])
                change_to = dateparser.parse(change[1][1])
                print(f"changes from {change_from.strftime('%d %b %Y, %H:%M %Z')[:-7]} to {change_to.strftime('%d %b %Y, %H:%M %Z')[:-7]}")
        if report["changes"]:
            print("\tCHANGED:")
        for c in report["changes"]:
            if c[0] != "captured_date":
                if type(c[0]) is list:
                    print(f"\t\t{'-'.join([str(name) for name in c[0]][:-1])}:")
                else:
                    print(f"\t\t{c[0]}:")
                print(f"\t\t\tFROM:\t{c[1][0]}")
                print(f"\t\t\tTO:\t{c[1][1]}")
        if report["added"]:
            print("\tADDED:")
        for a in report["added"]:
            if type(a[0]) is list:
                print(f"\t\t{'-'.join([str(name) for name in a[0]][:-1])}:")
            else:
                print(f"\t\t{a[0]}:")
            print(f"\t\t\t{newline.join(sorted(a[1][0][1]))}")
        if report["removed"]:
            print("\tREMOVED:")
        for r in report["removed"]:
            if type(r[0]) is list:
                print(f"\t\t{'-'.join([str(name) for name in r[0]][:-1])}:")
            else:
                print(f"\t\t{r[0]}:")
            print(f"\t\t\t{newline.join(sorted(r[1][0][1]))}")
    print("\n")
