# Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import argparse
import json
import sys
import time

try:
    from sasctl import Session
    from sasctl.services import model_publish as mp
    from sasctl.services import model_repository as mr
except Exception:
    print("In order to run this script you need to install the sasctl package")
    raise


def _print_json(title, data):
    print(f"\n=== {title} ===")
    try:
        print(json.dumps(data, indent=2, ensure_ascii=True, default=str))
    except Exception:
        print(str(data))


def _safe_response_body(response):
    # Try JSON first for structured SAS error payloads, then fallback to plain text.
    try:
        return response.json()
    except Exception:
        text = response.text.strip()
        return text if text else "<empty response body>"


parser = argparse.ArgumentParser(
    description="Debug script to publish LLMs and print detailed API diagnostics"
)
parser.add_argument(
    "-vs",
    "--viya_server",
    type=str,
    help="Enter the URL for the SAS Viya server. An example is example.sas.com",
    required=True,
)
parser.add_argument(
    "-u",
    "--username",
    type=str,
    help="Enter your username for the SAS Viya server",
    required=True,
)
parser.add_argument(
    "-p",
    "--password",
    type=str,
    help="Enter your password for the SAS Viya server",
    required=True,
)
parser.add_argument(
    "-l",
    "--llms",
    nargs="+",
    help=(
        "List of LLM names to publish - specify the subfolder model name "
        "(e.g., phi_3_mini_4k phi_35_mini)"
    ),
    required=True,
)
parser.add_argument(
    "-d",
    "--destination",
    type=str,
    help=(
        "Specify the target publishing destination name, has to be a "
        "container publishing destination - i.e. llmACR"
    ),
    required=True,
)
parser.add_argument(
    "-k",
    "--verify_ssl",
    type=str,
    default="true",
    help="Set to false if you have a self-signed certificate",
)
parser.add_argument(
    "--wait_seconds",
    type=int,
    default=1,
    help="Delay between model publish requests",
)
parser.add_argument(
    "--print_destination",
    action="store_true",
    help="Print destination metadata before publishing",
)
parser.add_argument(
    "--print_model_details",
    action="store_true",
    help="Print model details used to build payload",
)
args = parser.parse_args()


try:
    with Session(
        args.viya_server,
        args.username,
        args.password,
        verify_ssl=(args.verify_ssl.lower() == "true"),
    ) as s:
        destination = mp.get_destination(args.destination)
        if destination is None:
            raise ValueError(
                f"No valid destination name specified. Please check: {args.destination}"
            )

        destination_type = str(getattr(destination, "destinationType", ""))
        valid_destination_types = {
            "azure",
            "aws",
            "gcp",
            "privatedocker",
            "privateDocker",
            "AWS",
            "GCP",
        }
        if destination_type not in valid_destination_types:
            raise ValueError(
                f"The provided destination is not a valid SCR destination. "
                f"Found destinationType={destination_type}"
            )

        if args.print_destination:
            try:
                _print_json("Destination", dict(destination.items()))
            except Exception:
                _print_json("Destination", str(destination))

        for model in args.llms:
            print(f"\n----- Processing model: {model} -----")
            model_details = mr.get_model_details(model)

            if args.print_model_details:
                _print_json("Model Details", dict(model_details.items()))

            tags = list(model_details.get("tags", []))
            sizing_candidates = set(tags) & {"small", "medium", "large"}
            if sizing_candidates:
                sizing_tag = sizing_candidates.pop()
            else:
                print("No sizing tag found in model tags. Defaulting to small.")
                sizing_tag = "small"

            payload_obj = {
                "destinationName": args.destination,
                "modelContents": [
                    {
                        "modelName": model,
                        "publishLevel": "model",
                        "sourceUri": f"/modelRepository/models/{model_details['id']}",
                    }
                ],
                "name": model,
                "notes": "Published by LLM Framework - debug variant",
                "tags": [sizing_tag],
            }
            payload = json.dumps(payload_obj)
            headers = {
                "Content-Type": "application/vnd.sas.models.publishing.request.asynchronous+json",
                "Accept": "application/vnd.sas.models.publishing.publish+json",
            }

            _print_json("Request Payload", payload_obj)

            res = s.post("/modelManagement/publish?force=true", data=payload, headers=headers)

            print(f"HTTP Status: {res.status_code}")
            if res.status_code == 201:
                print(
                    f"The model {model} is being published to destination {args.destination}."
                )
                if args.wait_seconds > 0:
                    print(f"Waiting for {args.wait_seconds} second(s) before continuing...")
                    time.sleep(args.wait_seconds)
                continue

            print(
                f"Publish failed for model={model} destination={args.destination} "
                f"status={res.status_code}"
            )
            _print_json("Response Headers", dict(res.headers))
            _print_json("Response Body", _safe_response_body(res))

            # Keep processing additional models while preserving failure details.

except Exception as exc:
    print("Failed while trying to publish models with debug diagnostics enabled.")
    print(f"Error type: {type(exc).__name__}")
    print(f"Error message: {exc}")
    print(
        "Connection hint: verify server URL/username/password and optionally try -k false "
        "if SSL verification is the issue."
    )
    raise
