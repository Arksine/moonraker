#!/bin/bash
#  Helper script for completing service upgrades via ssh

ADDRESS="localhost"
PORT="7125"
API_KEY=""

# Python Helper Scripts
check_sudo_request=$( cat << EOF
import sys
import json
try:
  ret = json.load(sys.stdin)
except Exception:
  exit(0)
entries = ret.get('result', {}).get('entries', [])
for item in entries:
   if item['dismissed'] is False and item['title'] == 'Sudo Password Required':
     sys.stdout.write('true')
     exit(0)
sys.stdout.write('false')
EOF
)

check_pw_response=$( cat << EOF
import sys
import json
try:
  ret = json.load(sys.stdin)
except Exception:
  exit(0)
responses = ret.get('result', {}).get('sudo_responses', [])
if responses:
  sys.stdout.write('\n'.join(responses))
EOF
)

print_help_message()
{
    echo "Utility to complete privileged upgrades for Moonraker"
    echo
    echo "usage: finish-upgrade.sh [-h] [-a <address>] [-p <port>] [-k <api_key>]"
    echo
    echo "optional arguments:"
    echo "  -h                show this message"
    echo "  -a <address>      address for Moonraker instance"
    echo "  -p <port>         port for Moonraker instance"
    echo "  -k <api_key>      API Key for authorization"
}

while getopts "a:p:k:h" arg; do
    case $arg in
        a) ADDRESS=${OPTARG};;
        b) PORT=${OPTARG};;
        k) API_KEY=${OPTARG};;
        h)
          print_help_message
          exit 0
          ;;
    esac
done

base_url="http://${ADDRESS}:${PORT}"

echo "Completing Upgrade for Moonraker at ${base_url}"
echo "Requesting Announcements..."
ann_url="${base_url}/server/announcements/list"
curl_cmd=(curl -f -s -S "${ann_url}")
[ -n "${API_KEY}" ] && curl_cmd+=(-H "X-Api-Key: ${API_KEY}")
result="$( "${curl_cmd[@]}" 2>&1 )"
if [ $? -ne 0 ]; then
    echo "Moonraker announcement request failed with error: ${result}"
    echo "Make sure the address and port are correct.  If authorization"
    echo "is required supply the API Key with the -k option."
    exit -1
fi
has_req="$( echo "$result" | python3 -c "${check_sudo_request}" )"
if [ "$has_req" != "true" ]; then
    echo "No sudo request detected, aborting"
    exit -1
fi

# Request Password, send to Moonraker
echo "Sudo request announcement found, please enter your password"
read -sp "Password: " passvar
echo -e "\n"
sudo_url="${base_url}/machine/sudo/password"
curl_cmd=(curl -f -s -S -X POST "${sudo_url}")
curl_cmd+=(-d "{\"password\": \"${passvar}\"}")
curl_cmd+=(-H "Content-Type: application/json")
[ -n "$API_KEY" ] && curl_cmd+=(-H "X-Api-Key: ${API_KEY}")

result="$( "${curl_cmd[@]}" 2>&1)"
if [ $? -ne 0 ]; then
    echo "Moonraker password request failed with error: ${result}"
    echo "Make sure you entered the correct password."
    exit -1
fi
response="$( echo "$result" | python3 -c "${check_pw_response}" )"
if [ -n "${response}" ]; then
    echo "${response}"
else
    echo "Invalid response received from Moonraker.  Raw result: ${result}"
fi
