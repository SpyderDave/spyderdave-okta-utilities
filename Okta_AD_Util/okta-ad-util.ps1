
# 2 Modes of operation:

# 1. --TID <TID|XID> --FirstName <firstName> --LastName <lastName> --Email <email> 
#   In this mode, the user will be extracted from AD and Added or Updated in Okta
#   Optional; FirstName LastName Email will override AD
# 2. --CSV <Filename> 
#   In this mode, the users will be extracted from the CSV file and Added or Updated in Okta. 
# 3. --GetIDs <true|false>
#   In this mode, we connect to Corp AD and retrieve all corporate TID's and XID's and write them to a CSV

# Example:
# .\AD_TO_OKTA.ps1 --TID T123456 --FirstName John --LastName Doe --Email XXXXXXXXXXXXXXXXXX
# .\AD_TO_OKTA.ps1 --TID T123456
# .\AD_TO_OKTA.ps1 --CSV C:\TEMP\TID_XID_NotInAD.csv
# .\AD_TO_OKTA.ps1 --GetIDs 
# Optional :
# -D debug messages
# -Update enable update of existing users. By default existing user will be skipped
# -DryRun do not actually add or update users in Okta

[CmdletBinding()]
param (
    [string]$TID,
    [string]$FirstName,
    [string]$LastName,
    [string]$Email,
    [string]$CSV,
    [switch]$GetIDs = $false,
    [switch]$GetOktaUsers = $false,
    [switch]$Update = $false,
    [switch]$D = $false,
    [switch]$DryRun = $false,
    [switch]$Activate = $false,
    [switch]$DeleteUsersFromGroup = $false,
    [string]$GroupId
)
$Title = @"
_____     _                              _ _   _         ___ _                 _     ___ _       _    __                      
/__   \___| |_   _ ___    /\  /\___  __ _| | |_| |__     / __\ | ___  _   _  __| |   / _ \ | __ _| |_ / _| ___  _ __ _ __ ___  
  / /\/ _ \ | | | / __|  / /_/ / _ \/ _` | | __| '_ \   / /  | |/ _ \| | | |/ _` |  / /_)/ |/ _` | __| |_ / _ \| '__| '_ ` _ \ 
 / / |  __/ | |_| \__ \ / __  /  __/ (_| | | |_| | | | / /___| | (_) | |_| | (_| | / ___/| | (_| | |_|  _| (_) | |  | | | | | |
 \/   \___|_|\__,_|___/ \/ /_/ \___|\__,_|_|\__|_| |_| \____/|_|\___/ \__,_|\__,_| \/    |_|\__,_|\__|_|  \___/|_|  |_| |_| |_|
"@
Write-Host $Title

Write-Host "Importing Support Functions"
Import-Module $PSScriptRoot\OktaADSupport.psm1 -Force -DisableNameChecking

# Define the Okta API URL and API token
$global:oktaUrl = $env:OKTA_ORG_URL
$global:oktaApiToken = $env:OKTA_API_TOKEN

if ( -Not $global:oktaUrl) {
    Write-Debug "OKTA_ORG_URL environment variable not set"
    $global:oktaUrl = Read-Host "Enter Okta Org URL"
}
if ( -Not $global:oktaApiToken) {
    Write-Debug "OKTA_API_TOKEN environment variable not set"
    $global:oktaApiToken = Read-Host "Enter Okta API Token"
}


# Define the Okta API endpoint for creating a user
if ($Activate) {
    $global:url = "$oktaUrl/api/v1/users?activate=true"
} else {
    $global:url = "$oktaUrl/api/v1/users?activate=false"
}

Write-Host "===================================================="
Write-Host "Okta User Creation Tool"
Write-Host "===================================================="
if ($D) {
    $global:DebugPreference = "Continue"
} else {
    $global:DebugPreference = "SilentlyContinue"
}
$ErrorActionPreference = 'SilentlyContinue'

# Okta has an official PS module but it hangs on installing
#Set-PSRepository -Name 'PSGallery' -InstallationPolicy Trusted
#Install-Module -Name Okta.PowerShell

if (($TID) -And ($CSV)) {
    Write-Debug "Please provide either TID or CSV parameter, not both."
    exit
}

if ($CSV) {
    if ( $DeleteUsersFromGroup ) {
        DeleteUsersFromGroup $GroupId $CSV
        exit
    }
    else {
        $Mode = 2
    }
} 
elseif ($GetIDs) {
    $Mode = 3 
}
elseif ($GetOktaUsers) {
    $Mode = 4
}
else {
    if ((-Not $TID) -And (-Not $CSV)) {
        Write-Debug -ForegroundColor Red  "***** ERROR ERROR ERROR *****"
        Write-Debug "Please provide TID or CSV parameter."
        exit
    }
    else {
        $Mode = 1
    }
}

Write-Debug "-----------------------------------------------------------------------------------------------"
Write-Debug "Parameter Listing"
Write-Debug "CSV: $CSV"
Write-Debug "TID: $TID"
Write-Debug "FirstName: $FirstName"  
Write-Debug "LastName: $LastName"
Write-Debug "Email: $Email"  
Write-Debug "DryRun: $DryRun"
Write-Debug "-----------------------------------------------------------------------------------------------"

$LDAPUrl = 
# Set up LDAP connection details
$server = $env:LOGONSERVER.Replace("\","")
$ldapServer = "LDAP://" + $server.ToLower() + "." + $env:USERDNSDOMAIN.ToLower()
#$ldapServer = "LDAP://onads001.corp.ads"
$baseDN = "DC=corp,DC=ads"         # Replace with your domain components
# Build the LDAP path
$ldapPath = "$ldapServer/$baseDN"
# Initialize DirectorySearcher object
$directorySearcher = New-Object System.DirectoryServices.DirectorySearcher([ADSI]$ldapPath)

Write-Debug "-----------------------------------------------------------------------------------------------"
Write-Debug "Connection Properties:"
Write-Debug "LDAP Server: $ldapServer"
Write-Debug "Base DN: $baseDN"
Write-Debug "LDAP Path: $ldapPath"
Write-Debug "Directory Searcher: $directorySearcher"
Write-Debug "Okta URL: $oktaUrl"
Write-Debug "-----------------------------------------------------------------------------------------------"

#
# Mode 1 TID Passed on the command line. 
# 
if ($Mode -eq 1) {
    Write-Host "*************************************************************************"
    Write-Host "Single User Mode for UserID : $TID"
    Write-Host "*************************************************************************"

    # Get the user from Active Directory
    $directorySearcher.Filter = "(sAMAccountName=$TID)"

    # Perform the search
    $userResult = $directorySearcher.FindOne()

    # Check if user was found
    if ($userResult) {
        # Display the user's distinguished name and other attributes
        $user = $userResult.Properties
        #
        # Iterate over each property and print the name and values
        # uncomment to print all keys
        #
        # foreach ($property in $user.Keys) {
        #     Write-Debug "$property" 
        #     Write-Debug $($user[$property]).ToString()
        # }


        Write-Host "User data found in Active Directory:"
        Write-Host "First Name: $($user['givenName'])"
        Write-Host "Last Name: $($user['sn'])"
        Write-Host "Distinguished Name: $($user['distinguishedName'])"
        Write-Host "sAMAccountName: $($user['sAMAccountName'])"

        $manager_distinguishedName = "$($user['manager'])"
        if ($manager_distinguishedName) {
            Write-Debug "Looking up the Manager : $manager_distinguishedName"
            $distinguishedName = $user['manager']
            $directorySearcher.Filter = "(&(objectClass=user)(distinguishedName=$distinguishedName))"
            # Perform the search
            $managerResult = $directorySearcher.FindOne()
            if ($managerResult) {
                $manager = $managerResult.Properties
                Write-Debug "Manager data found in Active Directory: $($manager['displayName'])"
            }
        }
        elseif (-Not $manager_distinguishedName) {
            Write-Debug "Manager data not found in Active Directory"
            $oktaManager = "Unknown"
            $oktaManagerId = "Unknown"
        }
        else {
            Write-Debug -ForegroundColor Red "Manager Lookup Failed. Setting to Unknown"
            $oktaManager = "Unknown"
            $oktaManagerId = "Unknown"
        }

    } else {
        Write-Host -ForegroundColor Red "User not found! Exiting Gracefully."
        Write-Host -ForegroundColor Red "TID must be in the for Txxxxxx or Xxxxxxx"
        exit
    }
    # Check if the command line included any overrides.
    if ($LastName) { 
        $oktaLastName = $LastName
        $_OverRides = $_OverRides + "LastName=$LastName,"
    }
    else { 
        $oktaLastName = $($user['sn'])
    }

    if ($FirstName) { 
        $oktaFirstName = $FirstName
        $_OverRides = $_OverRides + "FirstName=$FirstName,"
    }
    else { 
        $oktaFirstName = $($user['givenName'])
    }
    if ($Email) { 
        $oktaEmail = $Email
        $oktaLogin = $Email
        $_OverRides = $_OverRides + "Email=$Email,"
    }
    else { 
        $oktaEmail = $($user['mail'])
        $oktaLogin = $($user['mail'])
    }
    if (-Not $oktaManager) {
        $oktaManager = $($manager['displayName'])
        $oktaManagerId = $($manager['mail']).ToLower()
    }

    Write-Debug "Creating or Updating TID: $TID from Active Directory"
    Write-Debug "Overridden Data: $_OverRides"

    # Prepare the user data for Okta
    # $cleanTitle = -join ($($user['title']).ToCharArray() | Where-Object { [int][char]$_ -ge 32 -and [int][char]$_ -le 126 })
    $oktaUser = @{
        "profile" = @{
            "employeeNumber"= $($user['samAccountName']).ToUpper()
            "firstName"     = $oktaFirstName
            "lastName"      = $oktaLastName
            "email"         = $oktaEmail.ToLower().ToLower()
            "login"         = $oktaLogin.ToLower()
            "displayName"   = $($user['displayName']) | % { Remove-Diacritics $_ }
            "manager"       = $oktaManager
            "managerId"     = $oktaManagerId
            "mobilePhone"   = if($($user['mobile']) -eq $null) { "" } else { $($user['mobile']) }
            "zipCode"       = $($user['postalCode'])
            "streetAddress" = $($user['streetAddress']) | % { Remove-Diacritics $_ }
            "city"          = $($user['l']) | % { Remove-Diacritics $_ }
            "state"         = $($user['st'])
            "title"         = $cleanTitle | % { Remove-Diacritics $_ }
        }
    }
    if (CreateOrUpdateOktaUser $oktaUser $DryRun) {
        Write-Debug "SUCCESS: $UserName - CREATED OR UPDATED IN OKTA"
        Log-Message "SUCCESS: $UserName - CREATED OR UPDATED IN OKTA"
    }
    else {
        Write-Debug "FAILED: $UserName - NOT CREATED OR UPDATED IN OKTA"
        Log-Message "FAILED: $UserName - NOT CREATED OR UPDATED IN OKTA"
    }
}
# Here we assume Mode = 2
elseif ($Mode -eq 2) { 
    Write-Host "*************************************************************************"
    Write-Host "CSV Mode - Reading file : $CSV"
    Write-Host "*************************************************************************"
    Write-Debug "Mode 2: Using CSV file: $CSV to Create or Update Users from Active Directory"
    Write-Debug "Info: Reading CSV File"
    $Header = 'user_name','first_name','last_name','display_name','email_address'
    # Check if the CSV exists
    $fileexists = Test-Path -Path $CSV
    if (-Not $fileExists) {
        Write-Debug -ForegroundColor Red "The file $CSV does not exist. Exitting"
        exit
        }
    $LoopCounter = 0
    Import-Csv -Path $CSV -Header $Header | ForEach {
        $LoopCounter++
        Start-Sleep -Milliseconds 100
        Write-Progress -Activity "Processing Users:" -Status "$LoopCounter Complete" -PercentComplete -1
        $UserName = $_.user_name.trim()
        Write-Debug "Retrieving Active Directory information for User Name: $UserName"
        # Get the user from Active Directory
        $directorySearcher.Filter = "(sAMAccountName=$UserName)"
        # Perform the search
        $userResult = $directorySearcher.FindOne()
        # Check if user was found
        if ($userResult) {
            # Display the user's distinguished name and other attributes
            $user = $userResult.Properties
            if ($($user['manager']) -ne $null) 
            {
                Write-Debug "Looking up the Manager : $($user['manager'])"
                $distinguishedName = $user['manager']
                $directorySearcher.Filter = "(&(objectClass=user)(distinguishedName=$distinguishedName))"
                # Perform the search
                $managerResult = $directorySearcher.FindOne()
                if ($managerResult) {
                    $manager = $managerResult.Properties
                    Write-Debug "Manager data found in Active Directory: $($manager['displayName'])"
                }
                else {
                    Write-Debug "***** Manager Lookup Failed *****"
                    $managerName = "N/A"
                    $managerId = "N/A"
                }
                $managerName = $($manager['displayName']) | % { Remove-Diacritics $_ }
                $managerId = $($manager['mail'].ToLower()) 
                Clear-Variable -name managerResult
            }
            else {
                $managerName = "N/A"
                $managerId = "N/A"
                Write-Debug "Manager data not found in Active Directory"
            }


            
            $oktaUser = @{
                "profile" = @{
                    "employeeNumber"= $($user['samAccountName']).ToUpper()
                    "firstName"     = $($user['givenName']) | % { Remove-Diacritics $_ }
                    "lastName"      = $($user['sn']) | % { Remove-Diacritics $_ }
                    "email"         = $($user['mail']).ToLower()
                    "login"         = $($user['mail']).ToLower()
                    "displayName"   = $($user['displayName']) | % { Remove-Diacritics $_ }
                    "title"         = $($user['title']) | % { Remove-Diacritics $_ }
                    "manager"       = $managerName
                    "managerId"     = $managerId
                    "mobilePhone"   = if($($user['mobile']) -eq $null) { "" } else { $($user['mobile']) }
                    "zipCode"       = $($user['postalCode'])
                    "streetAddress" = $($user['streetAddress']) | % { Remove-Diacritics $_ }
                    "city"          = $($user['l']) | % { Remove-Diacritics $_ }
                    "state"         = $($user['st'])                }
            }
            Log-Message "Processing Okta User: $($user['givenName']),$($user['sn']),$($user['mail']),$($user['mobile'])"
            if (CreateOrUpdateOktaUser $oktaUser $DryRun) {
                Write-Debug "SUCCESS: $UserName - CREATED OR UPDATED IN OKTA"
                Log-Message "SUCCESS: $UserName - CREATED OR UPDATED IN OKTA"
            }
            else {
                Write-Debug "FAILED: $UserName - NOT CREATED OR UPDATED IN OKTA"
                Log-Message "FAILED: $UserName - NOT CREATED OR UPDATED IN OKTA"
            }
        }
        else {
            Write-Debug "FAILED: $UserName - NOT FOUND IN ACTIVE DIRECTORY"
            Log-Message "FAILED: $UserName - NOT FOUND IN ACTIVE DIRECTORY"
        }
    Clear-Variable -name userResult
    }
}
elseif ($Mode -eq 3) { 
    Write-Host "*************************************************************************"
    Write-Host "Active Directory User ID Retrieval Mode"
    Write-Host "*************************************************************************"    
    $OutFile = $PSScriptRoot + "\" + $LogDate + ".txt"
    Write-Host "Output File: $Outfile"

    # Set up LDAP connection details
    $ldapServer = "LDAP://onads001.corp.ads"  # Replace with your LDAP server address
    $baseDN = "OU=Emergis,OU=Client Users,DC=corp,DC=ads"         # Replace with your domain components
    # Build the LDAP path
    $ldapPath = "$ldapServer/$baseDN"
    # Initialize DirectorySearcher object
    $searcher = New-Object System.DirectoryServices.DirectorySearcher([ADSI]$ldapPath)
    Write-Host "Getting all users from : $ldapPath"

    # Filter to get only user objects
    $searcher.Filter = "(&(objectClass=user)(objectCategory=person))"

    # Specify the properties to retrieve (only samAccountName in this case)
    $searcher.PropertiesToLoad.Add("samAccountName") | Out-Null

    # Perform the search
    $results = $searcher.FindAll()

    # Iterate through the results and display the samAccountName
    $LoopCounter = 0
    foreach ($result in $results) {
        $LoopCounter++
        Write-Progress -Activity "Processing Users:" -Status "$LoopCounter Complete" -PercentComplete -1
        $samAccountName = $result.Properties["samAccountName"]
        if ($samAccountName) {
            #$samAccountName
            Add-Content -Path $Outfile "$samAccountName"
        }
    }
    # Clean up
    $searcher.Dispose()
}
else {

    Write-Host "*************************************************************************"
    Write-Host "Okta User ID Retrieval Mode"
    Write-Host "*************************************************************************"    # Get users and display them
    $usersList = Get-OktaUsers -emailDomain "example.com" -customProfileField "employeeNumber"
    $usersList | Format-Table -AutoSize
    $Now = (Get-Date).tostring("yyyyMMddhhmmss")
    $outFile = $PSScriptRoot + "\oktaUsers-" + $Now + ".csv"
    $usersList | Export-Csv -Path $OutFile -NoTypeInformation
    Write-Host "List exported to : $outFile"
}
exit
