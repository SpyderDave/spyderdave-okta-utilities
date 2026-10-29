# PURPOSE

Powershell script to allow the management of Okta Users with seamless updating from the Telus Corp AD

# Requirements

You must run this script from a Telus Managed Windows workstation/laptop.

To use this script, you must set two environment variables:

OKTA_ORG_URL
OKTA_API_TOKEN

Example:

**Powershell**

$env:OKTA_ORG_URL="https://telushealth-oie.oktapreview.com"

$env:OKTA_API_TOKEN="my_super_scary_token"

# Get Yourself an Okta Token

Note: you must have admin permissions in Okta to create an API Token.

Login to the Okta Admin Console
Select Security--->API
Select the Tokens Tab
"Create Token" and follow the prompts.

NOTE: Your token should be protected! Do not share it. Any action performed with the token will be traced to your Okta Identity!

# Running the Script

** Modes of operation:**

--TID <TID|XID> --FirstName <firstName> --LastName <lastName> --Email <email>

In this mode, the user will be extracted from AD and Added or Updated in Okta
Optional; FirstName LastName Email will override AD

--CSV

In this mode, the users will be extracted from the CSV file and Added or Updated in Okta.

--GetIDs <true|false>

In this mode, we connect to Corp AD and retrieve all Telus Health TID's and XID's and write them to a CSV

Examples:
.\AD_TO_OKTA.ps1 --TID T123456 --FirstName John --LastName Doe --Email john.doe@nodomain.com
.\AD_TO_OKTA.ps1 --TID T123456
.\AD_TO_OKTA.ps1 --CSV C:\TEMP\TID_XID_NotInAD.csv
.\AD_TO_OKTA.ps1 --GetIDs

Optional Globals :
-D debug messages
-Update enable update of existing users. By default existing user will be skipped
-DryRun do not actually add or update users in Okta
