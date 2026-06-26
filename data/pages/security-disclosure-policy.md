---
url: https://stratpoint.com/security-disclosure-policy/
title: SECURITY DISCLOSURE POLICY
crawled_at: 2026-06-26T11:20:30Z
lastmod: 2025-07-01T07:54:22+00:00
content_hash: sha256:ae1d5bd9aae8f0341791742c6c575f7c92328bcfb06134542099ab0cc48a3bc5
---
# SECURITY DISCLOSURE POLICY

Stratpoint greatly appreciates investigative work into security vulnerabilities which is carried out by well-intentioned, ethical security researchers. We are committed to thoroughly investigating and resolving security issues in our platform and services in collaboration with the security community. This document aims to define a method by which Stratpoint can work with the security research community to improve our online security.

###### **Scope**

Vulnerabilities in Stratpoint products and services are only within scope of the Bug Bounty Scheme when they meet the following conditions:

* They have not been previously reported or have not already been discovered by our own internal procedures;
* It can be demonstrated that there would be a real impact to Stratpoint, its users or its customers should the vulnerability reported be exploited by a malicious actor. The existence of a vulnerability does not necessarily demonstrate that such a potential impact exists: theoretical impacts will not be considered as within the scope of the scheme;
* It exists within a domain that has a security.txt file in its root. Subdomains are considered in scope provided their parent domain is in scope. (i.e. The existence of: https://<stratpoint.com>/security.txt means that subdomain.stratpoint.com and www.stratpoint.com are also in scope.)

The following security issues are currently not in scope (please don’t report them):

* Volumetric/Denial of Service vulnerabilities (i.e. simply overwhelming our service with a high volume of requests);
* TLS configuration weaknesses (e.g. “weak” cipher suite support, TLS1.0 support, sweet32 etc.);
* Reports indicating that our services do not fully align with “best practice” (e.g. missing security headers or suboptimal email-related configurations such as SPF, DMARC etc.);
* Issues surrounding the verification of email addresses used to create user accounts;
* Clickjacking vulnerabilities;
* Self XSS (i.e. where a user would need to be tricked into pasting code into their web browser);
* CSRF where the resulting impact is minimal;
* CRLF attacks where the resulting impact is minimal;
* Host header injection where the resulting impact is minimal;
* Network data enumeration techniques (e.g. banner grabbing, existence of publicly available server diagnostic pages);
* Reports of improper session management / session fixation vulnerabilities.

###### **Bug Bounty**

As a token of our gratitude for your assistance, we will try to offer (if budget permits) a reward for every report of a security problem that was not yet known to us and within scope as described in the previous section. The amount of the reward will be determined based on the severity of the leak and the quality of the report.

###### **Reporting a vulnerability**

If you have discovered an issue which you believe is an in-scope security vulnerability (please see section 2 above for more detail on scope), please email security@stratpoint.com including:

* The website or page in which the vulnerability exists.
* A brief description of the class (e.g. “XSS vulnerability”) of the vulnerability. Please avoid including any details which would allow reproduction of the issue at this stage. Details will be requested subsequently, over encrypted communications.

In accordance with industry convention, we ask that reporters provide a benign (i.e. non-destructive) proof of exploitation wherever possible. This helps to ensure that the report can be triaged quickly and accurately whilst also reducing the likelihood of duplicate reports and/or malicious exploitation for some vulnerability classes (e.g. sub-domain takeovers). Please ensure that you do not send your proof of exploit in the initial, plaintext email if the vulnerability is still exploitable. Please also ensure that all proof of exploits are in accordance with our guidance (below), if you are in any doubt, please email security@stratpoint.com for advice.

Please read this document fully prior to reporting any vulnerabilities to ensure that you understand the policy and can act in compliance with it.

###### **What to expect**

In response to your initial email to security@stratpoint.com you will receive an acknowledgement reply email from Stratpoint Security Team, this is usually within 24 hours of your report being received. The acknowledgment email will include a ticket reference number which you can quote in any further communications with our Security Team. Attached to the acknowledgement email will be a PGP key which you can use to encrypt future communications containing sensitive information.

Following the initial contact, our Security Team will work to triage the reported vulnerability and will respond to you as soon as possible to confirm whether further information is required and/or whether the vulnerability qualifies as per the above scope, or is a duplicate report. From this point, necessary remediation work will be assigned to the appropriate Stratpoint teams and/or supplier(s). Priority for bug fixes and/or mitigations will be assigned based on the severity of impact and complexity of exploitation. Vulnerability reports may take some time to triage and/or remediate, you’re welcome to enquire on the status of the process but please limit this to no more than once every 14 days, this helps our Security team focus on the reports as much as possible.

Our Security Team will notify you when the reported vulnerability is resolved (or remediation work is scheduled) and will ask you to confirm that the solution covers the vulnerability adequately. We will offer you the opportunity to feed back to us on the process and relationship as well as the vulnerability resolution. This information will be used in strict confidence in order to help us improve the way in which we handle reports and/or develop services and resolve vulnerabilities. We will also offer to include reporters of qualifying vulnerabilities on our acknowledgments page and we’ll ask for the details you wish to be included.

###### **Guidance**

Security researchers must not:

* Access unnecessary amounts of data. For example, 2 or 3 records is enough to demonstrate most vulnerabilities (such as an enumeration or direct object reference vulnerability);
* Violate the privacy of Stratpoint users, staff, contractors, systems etc. For example by sharing, redistributing and/or not properly securing data retrieved from our systems or services;
* Communicate any vulnerabilities or associated details via methods not described in this policy or with anyone other than your dedicated Stratpoint security contact;
* Modify data in our systems/services which is not your own;
* Disrupt our service(s) and/or systems; or
* Disclose any vulnerabilities in Stratpoint systems/services to 3rd parties/the public prior to Stratpoint confirming that those vulnerabilities have been mitigated or rectified. This does not prevent notification of a vulnerability to 3rd parties to whom the vulnerability is directly relevant, for example where the vulnerability being reported is in a software library or framework – but details of the specific vulnerability of Stratpoint must not be referenced in such reports. If you are unsure about the status of a 3rd party to whom you wish to send notification, please email security@stratpoint.com for clarification.

We request that any and all data retrieved during research is securely deleted as soon as it is no longer required and at most, 1 month after the vulnerability is resolved, whichever occurs soonest.

If you are unsure at any stage whether the actions you are thinking of taking are acceptable, please contact our security team for guidance (please do not include any sensitive information in the initial communications): [security@stratpoint.com](mailto:security@stratpoint.com).

###### **Legalities**

This policy is designed to be compatible with common good practice among well-intentioned security researchers. It does not give you permission to act in any manner that is inconsistent with the law or cause Stratpoint to be in breach of any of its legal obligations, including but not limited to:

* Republic Act No. 10173 also known as The Data Privacy Act of 2012
* Republic Act No. 10175 also known as Cybercrime Prevention Act of 2012

Stratpoint will not seek prosecution of any security researcher who reports, in good faith and in accordance with this policy, any security vulnerability on an in-scope Stratpoint service.

###### **Feedback**

If you wish to provide feedback or suggestions on this policy, please contact our security team: security@stratpoint.com. This policy will evolve over time and your input will be valued to ensure that it is clear, complete and remains relevant.
