# [SOP-ACC-01] VPN authentication failure after MFA re-enrolment

**Category:** platform_support_access
**Applies to:** vpn mfa lockout

## Symptom

I reset my authenticator app yesterday and now the VPN client rejects my login. It shows 'authentication failed' straight after the push notification.

## Root cause

Stale VPN device certificate after MFA re-enrolment.

## Resolution steps

1. Confirmed the MFA re-enrolment timestamp in the identity console.
2. Cleared the cached device certificate from the VPN client profile.
3. Had the user sign out fully and re-authenticate to reissue the certificate.
4. Verified connection from the user's machine before closing.

## Before you close

- Confirm the outcome with the requester, in their words, not yours.
- If the requester's tier is Gold, hand the close to the named owner for that
  business unit rather than closing directly.
