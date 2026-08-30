#!/usr/bin/env python3
"""
FIX FOR POWERSHELL ERROR: -AllowedAppIds parameter not found

The old method (Set-SPOTenant -AllowedAppIds) no longer works.
Use the MODERN approach below instead.
"""

print("=" * 80)
print("SHAREPOINT MIGRATION: CORRECT FIX FOR 401 ERRORS")
print("=" * 80)

print("""
[PROBLEM]
  Old parameter 'AllowedAppIds' no longer exists in SharePoint Online module
  
[SOLUTION - Use Azure AD Admin Consent]
  The app already has the correct permissions configured in Azure AD.
  You just need to grant ADMIN CONSENT in the target tenant.

═══════════════════════════════════════════════════════════════════════════════

[OPTION 1] Grant Consent via Azure AD Portal (EASIEST) ✓ Recommended
─────────────────────────────────────────────────────────────────────────────

  1. Login to Azure Portal as Target Tenant Admin:
     https://portal.azure.com
  
  2. Go to: Azure Active Directory > App registrations > All applications
  
  3. Search for app: "tenant-migrator" 
     (Or search by ID: 294bb4f3-fb36-49bf-a2a3-fd6fdbfe703c)
  
  4. Click on the app
  
  5. Left menu: API permissions
  
  6. Button: "Grant admin consent for [tenant name]"
  
  7. Click YES to confirm
  
  ✓ Done! Wait 5-10 minutes for replication


═══════════════════════════════════════════════════════════════════════════════

[OPTION 2] Grant Consent via PowerShell (Alternative)
─────────────────────────────────────────────────────────────────────────────

  # Install module if needed
  Install-Module -Name Microsoft.Graph -Force

  # Connect to Azure AD
  Connect-MgGraph -TenantId "1f2a2d8f-83cf-4fbd-b389-59ed2dbc280a" -Scopes "Application.ReadWrite.All"

  # Grant admin consent
  $appId = "294bb4f3-fb36-49bf-a2a3-fd6fdbfe703c"
  Update-MgApplication -ApplicationId $appId -RequiredResourceAccess @{
    ResourceAppId = "00000003-0000-0ff1-ce00-000000000000"  # SharePoint Graph API
    ResourceAccess = @(
      @{Id = "e1fe6dd8-ba31-4d61-89e7-88639da4683d"; Type = "Scope"}  # Sites.ReadWrite.All
    )
  }


═══════════════════════════════════════════════════════════════════════════════

[OPTION 3] Use Modern SharePoint Admin API (Most Reliable)
─────────────────────────────────────────────────────────────────────────────

  # In PowerShell as target tenant admin:
  
  Connect-SPOService -Url https://paizesoffice-admin.sharepoint.com
  
  # Get the app
  Get-SPOAppErrors
  
  # Or try the modern parameter:
  Set-SPOTenant -ConditionalAccessPolicy BlockManagedDeviceNonCompliantOrUnregistered

  # For app permissions, use Graph API instead (more reliable)


═══════════════════════════════════════════════════════════════════════════════

[RECOMMENDED: Option 1 (Azure Portal) - Follow these steps:]

  1. Go to: https://portal.azure.com
  2. Search box: "App registrations"
  3. Click: "All applications"  
  4. Search: "294bb4f3-fb36-49bf-a2a3-fd6fdbfe703c"
  5. Click the app name
  6. Left menu: "API permissions"
  7. Button at top: "Grant admin consent for [YourTenantName]"
  8. Accept the confirmation
  9. Status changes to GREEN checkmark
  10. Wait 5-10 minutes
  11. Then run migration retry

═══════════════════════════════════════════════════════════════════════════════
""")

print("\nAfter granting consent in Azure AD, run migration retry:")
print("""
  tenant-migrator --state sharepoint-pilot.sqlite batch \\
    --config .\sharepoint-pilot.yaml \\
    --report sharepoint-pilot-result-after-permissions.json
""")

print("=" * 80)
