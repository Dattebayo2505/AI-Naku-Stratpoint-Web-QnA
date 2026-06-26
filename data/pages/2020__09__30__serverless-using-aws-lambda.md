---
url: https://stratpoint.com/2020/09/30/serverless-using-aws-lambda/
title: Serverless using AWS Lambda
crawled_at: 2026-06-26T15:37:11Z
lastmod: 2022-03-07T04:42:19+00:00
content_hash: sha256:24a99d970e2994b59196a226af38563a16910d23ccdcff485619a4e78930d57d
---
# Serverless using AWS Lambda

![sample-blog](https://stratpoint.com/wp-content/uploads/sample-blog-image-scaled.jpg "Young men testing virtual reality technology")

### Introduction

### 

### 

The rapid pace of technological change and global competition means software release cycles have become even more compressed such that speed is the #1 concern for any technology business or solution.

This is where Stratpoint recommends a new architectural design solution, known as **Serverless Microservices Architecture**. Amazon, with its AWS Lambda offering, provides a unique way of designing applications into different small components, which can be independently deployed as individual services, eliminating the need to manage a single application server and, therefore, having to deploy one big complex application like in the past. Those services are simply triggered by any other backend or front end action only when needed.

Major advantages to Serverless Architecture include reduced operational and development costs, easier operational management, and reduced environmental impact. This approach moves much behavior to the front end and therefore focuses on user interaction and performance.

This will greatly provide an overall impact on the solution with easy development life-cycle processes and high performance deployment efficiency.

### The Challenge

**Feature heavy solution**

For a Philippine Telco, it’s Customer Self-service solution was envisioned to be the one-stop- shop where the subscriber can fully-manage their accounts either through a web portal or provided mobile apps: manage their account, buy load (prepaid credit), share-a-load, view and avail of promotions, view data usage and call/text consumptions, view bills, contest bill line items, upgrade their plan, and view rewards points.. In the future, additional features such as bills payment, purchasing phones, performing e-commerce transaction, and redemption of loyalty points will also be supported.

**Integrating to various channels and platforms to create a single portal experience**

The portal integrates with various services and platforms within the Telco’s ecosystem. The existing APIs were built from the traditional monolithic structure. It is a set of load balancers, multiple EC2s, RDS, and Java. The upgraded application will need to consider a uniform way of connecting to the various internal systems and allow for additions in the future.

**Slow response time**

Another challenge lies in the volume of traffic the application is expected to generate considering that the Telco’s subscriber base is close to 53 million. As features are still being added, the application naturally became too fat (memory), slow performing and hard to maintain.

**Growing Infrastructure and operational cost**

Infrastructure and hardware are a necessary component of any IT system, but they’re often also a distraction from what should be the core focus—solving the business problem. Slowness, data connection problems, managing and monitoring several application servers manually, are just some of the problems using a traditional architecture. All servers involved need to be managed, maintained, patched, and backed up at any time, geographical redundancy will even complicate those IT processes. This generates higher operational cost if more servers are added due to additional application complexity.

### Our Solution

Using AWS Lambda and an updated microservices development and deployment approach as described below, Stratpoint strongly believes this will enhance performance, usability and customer interaction significantly:

Before:

![aws solutions before](https://3p4expkcmfr6hgud4mqt.stratpoint.com/wp-content/uploads/AWS-solutions-before.png "AWS solutions before")

After:

![AWS Solutions After](https://3p4expkcmfr6hgud4mqt.stratpoint.com/wp-content/uploads/AWS-solutions-after.png "AWS solutions after")

**Managing API: Amazon API Gateway**

The first step was re-creating all APIs from the ground up, moving from traditional APIs to microservices. Stratpoint created different deployment stages, like dev, beta, and prod by using an API Gateway.

To call the API, we generated a platform-specific and language-specific SDK for the API. Currently, the API Gateway supports generating an SDK for an API, deployed to a specific stage, in Java, in JavaScript, in Java for Android, and in Objective-C or Swift for iOS.

**Handling Services: Lambda**

The Lambda functions contain the logic needed by the application. When it runs, the functions receive data as part of the Lambda event sent by the mobile app or web client.

**Security: Amazon Cognito**

Every API call must be authorized and authenticated. This is where Amazon Cognito Federated Identities comes into place. Cognito will respond with a unique Cognito ID and an OpenID Connect token for the end user. It is valid for five minutes, but it could be configured to a maximum time of up to 24 hours.

Any AWS Lambda function needs to be exposed via the API Gateway to be accessible via HTTP or other connectors.

### The Benefits

**Limited need for DevOps and SysAds**

Using the above described architectural approach, less resources are needed to manage, maintain and monitor the solution’s infrastructure. The only need for DevOps is for setting up the IAM roles and initial configurations of the needed Amazon services.

**Faster performance**

Every function is completely shielded from the rest of the code and the same function can fire in parallel in almost infinite numbers — completely automated.

In our case, the live serving of content is handled without “moving parts” at all. This makes the solution perform faster and more robust.

See the following chart comparing the previous setup (Old API) to Lambda (New API) in milliseconds (ms)

![AWS Functions](https://3p4expkcmfr6hgud4mqt.stratpoint.com/wp-content/uploads/AWS-Functions.png "AWS Functions")

**Managing API: Amazon API Gateway**

The first step was re-creating all APIs from the ground up, moving from traditional APIs to microservices. Stratpoint created different deployment stages, like dev, beta, and prod by using an API Gateway.

To call the API, we generated a platform-specific and language-specific SDK for the API. Currently, the API Gateway supports generating an SDK for an API, deployed to a specific stage, in Java, in JavaScript, in Java for Android, and in Objective-C or Swift for iOS.

**Handling Services: Lambda**

The Lambda functions contain the logic needed by the application. When it runs, the functions receive data as part of the Lambda event sent by the mobile app or web client.

**Security: Amazon Cognito**

Every API call must be authorized and authenticated. This is where Amazon Cognito Federated Identities comes into place. Cognito will respond with a unique Cognito ID and an OpenID Connect token for the end user. It is valid for five minutes, but it could be configured to a maximum time of up to 24 hours.

Any AWS Lambda function needs to be exposed via the API Gateway to be accessible via HTTP or other connectors.

**Pay for actual usage, not for uptime**

With the new serverless setup, the Telco will primarily pay for data transfer through API Gateway, and per 100 milliseconds that the Lambda functions run. Since we know on average what a new customer uses, we can calculate the costs per API call exactly.

The new API Gateway + Lambda based solution reached ~20 million of API calls per month on average.

Here’s the monthly price comparison of the Amazon EC2-based server vs Lambda Functions:

|  |  |  |
| --- | --- | --- |
| EC2 server | M3.xlarge – $0.392 per Hour \* 10 servers  = ($0.392 \* 24 hours) \* 30 days \* 10 servers | $2,822.40 |
| API Gateway + Lambda | $4.25 per million API calls  $0.12/GB for the first 10 TB  $0.054/hour for 1.6GB Caching (optional)  $0.000000834/100 ms(milliseconds) of API time execution with free up to 3.2M ms per month (depending on configuration)   For the API Gateway:  API call charges: 20 million \* 4.25/million = $85.00  Data transfer charges: 4 KB \* 20M = 80M/KB = 19.5 GB \* $0.09 = $1.76  TOTAL COSTS: $85 + $1.76 = $86.76  For Lambda:  Number of Executions: 20,000,000  Allocated Memory (MB): 512  Estimated Execution Time (ms): 1000  TOTAL COSTS: $163.83/month  \*Ref:https://s3.amazonaws.com/lambda-tools/pricing- calculator.html | $250.29 |



#### September 30, 2020



#### [Case Studies](https://stratpoint.com/./case-studies/)

## Blogs

[← Prev: Solaire Resort & Casino Managed Services](https://stratpoint.com/2020/09/29/solaire-resort-and-casino-managed-services/)

[Next: Gold’s Gym Japan Migration →](https://stratpoint.com/2020/09/30/golds-gym-japan-migration/)

╳

![]()
