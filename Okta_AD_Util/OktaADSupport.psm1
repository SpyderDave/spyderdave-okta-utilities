#
# THCP Okta / AD Support Functions
#
$LogDate = (Get-Date).tostring("yyyyMMddhhmmss")

function GetADUSer($id){
    Write-Host "Hello"
}
Function Log-Message() {
 param
    (
    [Parameter(Mandatory=$true)] [string] $Message
    )
 
    Try {
        #Frame Log File with Current Directory and date
        $LogFile = $PSScriptRoot + "\" + $LogDate + ".txt"
 
        #Add Content to the Log File
        $TimeStamp = (Get-Date).toString("dd/MM/yyyy HH:mm:ss:fff tt")
        $Line = "$TimeStamp - $Message"
        Add-content -Path $Logfile -Value $Line
 
        Write-Debug "Message: '$Message' Has been Logged to File: $LogFile"
    }
    Catch {
        Write-Debug -f Red "Error:" $_.Exception.Message
    }
}
Function CreateOrUpdateOktaUser($oktaUser,$DryRun){

    # Convert the data to JSON format
    $jsonBody = $oktaUser | ConvertTo-Json -Depth 3
    $headers = @{
        "Authorization" = "SSWS $oktaApiToken"
        "Content-Type" = "application/json"
    }
    # First lets check if the user exists in Okta
    $oktaUserExists = $False
    # Okta API URL for the user search
    $searchUser = $oktaUser['profile']['email']
    Write-Debug "Searching for user: $searchUser"
    $oktaUserApiUrl = "$oktaUrl/api/v1/users/$searchUser"
    # Send a GET request to Okta API to check if the user exists
    try {
        $response = Invoke-RestMethod -Uri $oktaUserApiUrl -Headers $headers -Method Get
        # If the user exists, the API will return the user object, so we return True
        $oktaUserExists = $true
        $existingOktaUser = $response[0].id
    } catch {
        # If the user doesn't exist, the API will return a 404, we catch the error and return False
        if ($_.Exception.Response.StatusCode -eq 404) {
            $oktaUserExists = $false
        } else {
            # Handle other errors (e.g., authentication issues)
            Write-Error "Error occurred: $($_.Exception.Message)"
            return $false
        }
    }

    if (-Not $oktaUserExists){
        # Send a POST request to Okta to create the user
        try {
            if (-Not $DryRun) {
                $response = Invoke-RestMethod -Uri $url -Method Post -Headers $headers -Body $jsonBody
                Write-Debug "User successfully created in Okta: $($response.profile.login)"
                Log-Message "User successfully created in Okta: $($response.profile.login)"
            }
        } catch {
            $line = ($oktaUser | ConvertTo-Json -Compress)
            Write-Debug "Failed to create user in Okta: $line"
            Log-Message "Failed to create user with error $_ in Okta: $line"
        }
    }
    else {
        if ($Update -eq $False) {
            Write-Debug "Updates Disabled: skipping UserID : $existingOktaUser"
            return $true
        }
        # Send a POST request to Okta to Update the user
        try {
            $userProfileSource = GetOktaUserProfileSource($existingOktaUser)
            if ($userProfileSource -eq "OKTA" -And -Not $DryRun) {
                Write-Host -ForegroundColor Blue "I will update $($oktaUser.profile.login) UserID : $existingOktaUser"
                $url = "$oktaUrl/api/v1/users/$existingOktaUser"
                $jsonBody = $oktaUser | ConvertTo-Json -Depth 3
                $response = Invoke-RestMethod -Uri $url -Method Post -Headers $headers -Body $jsonBody
                Write-Debug "User successfully updated in Okta: $($response.profile.login)"
                Log-Message "User successfully updated in Okta: $($response.profile.login)"
                return $true
            }
            else {
                Write-Host -ForegroundColor Red  "User is Sourced from Active Directory: $($oktaUser.profile.login) - skipping."
                Log-Message "User is Sourced from Active Directory: $($oktaUser.profile.login) - skipping."
            }
        } catch {
            $line = ($oktaUser | ConvertTo-Json -Compress)
            Write-Debug "Failed to update user in Okta: $line"
            Log-Message "Failed to update user with error $_ in Okta: $line"
            return $false
        }
    }
    return $true
}
# Function to retrieve users from Okta API
function Get-OktaUsers {
    param (
        [string]$emailDomain,
        [string]$customProfileField
    )

    $headers = @{
        "Authorization" = "SSWS $oktaApiToken"
        "Content-Type" = "application/json"
    }

    $usersUrl = "$oktaUrl/api/v1/users"

    # Initialize variables for pagination
    $allUsers = @()
    $nextUrl = $usersUrl

    do {
        # Send GET request to Okta API
        $response = Invoke-RestMethod -Uri $nextUrl -Method Get -Headers $headers

        # Add current page of users to the allUsers array
        $allUsers += $response

        # Check if there is a 'next' link for pagination
        $nextUrl = if ($response.PSObject.Properties["links"].Value.next) {
            $response.PSObject.Properties["links"].Value.next.href
        } else {
            $null
        }

        # Optionally, sleep for 200ms to avoid rate-limiting issues (Okta may enforce limits)
        Start-Sleep -Seconds 0.2

    } while ($nextUrl)

    # Filter users by email domain and include custom profile field
    $filteredUsers = $allUsers | Where-Object { $_.profile.email -like "*@$emailDomain" }
    #$filteredUsers = $allUsers

    $output = $filteredUsers | Select-Object `
        @{Name=$customProfileField; Expression={if ($_.profile.PSObject.Properties[$customProfileField]) {$_.profile.$customProfileField} else {""}}},
        @{Name="Username"; Expression={$_.profile.login}},
        @{Name="Email"; Expression={$_.profile.email}},
        @{Name="Manager"; Expression={$_.profile.manager}},
        @{Name="Manager Email"; Expression={$_.profile.managerId}}


    return $output
}
function GetOktaUserProfileSource($userId) {
    $headers = @{
        "Authorization" = "SSWS $oktaApiToken"
        "Content-Type" = "application/json"
    }
    # Construct the API request URL
    $uri = "$oktaUrl/api/v1/users/$userId"
    # Make the API request to get user details
    try {
        $response = Invoke-RestMethod -Uri $uri -Method Get -Headers $headers
    } catch {
        Write-Error "Error fetching user data: $_"
        exit
    }

    # Check if the profile is sourced from Active Directory
    $identityProvider = $response.credentials.provider.type
    #Write-Host "$identityProvider"
    return $identityProvider
}
function Remove-Diacritics {
    param ([String]$src = [String]::Empty)
      $normalized = $src.Normalize( [Text.NormalizationForm]::FormD )
      $sb = new-object Text.StringBuilder
      $normalized.ToCharArray() | % { 
        if( [Globalization.CharUnicodeInfo]::GetUnicodeCategory($_) -ne [Globalization.UnicodeCategory]::NonSpacingMark) {
          [void]$sb.Append($_)
        }
      }
      $sb.ToString()
    }

function DeleteUsersFromGroup {
    param (
        [string]$groupId,
        [string]$csvFilePath
    )
    $headers = @{
        "Authorization" = "SSWS $oktaApiToken"
        "Content-Type" = "application/json"
    }
    $Header = 'email_address'
    Write-Host "Reading CSV File : $csvFilePath"
    # $emails = Import-Csv -Path $csvFilePath -Encoding UTF8 -Header $Header 
    Write-Host "Removing users from group $groupId"

    Import-Csv -Path $csvFilePath -Header $Header | ForEach {
        $email = $_.email_address.trim() 
        Write-Host "Processing email address : $email"
        $myUri =  "$oktaUrl/api/v1/users?filter=profile.email eq ""$email"""
        Write-Host $myUri
        $response = Invoke-RestMethod -Uri $myUri -Method Get -Headers $headers
        if ($response.id) {
                $userId = $response.id
                Write-Host "User ID: $userId"
                # Remove the user from the group
                $groupUrl = "$oktaUrl/api/v1/groups/" + $groupId + "/users/" + $userId 
                $response = Invoke-RestMethod -Uri $groupUrl -Method delete -Headers $headers
                Write-Host "Removed $($email.Email) from group $groupId"
        } else {
                Write-Host "No user found with the email address $emailAddress"
            }
    }    
}