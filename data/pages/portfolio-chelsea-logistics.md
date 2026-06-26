---
url: https://stratpoint.com/portfolio-chelsea-logistics/
title: Chelsea Logistics Web Portal and AWS Data | Stratpoint Portfolio
crawled_at: 2026-06-26T11:20:30Z
lastmod: 2025-09-08T12:41:51+00:00
content_hash: sha256:e16f56c4502293d58f2957662575f7b3417e4c07db545b9b7c80c96b4b7ec527
---
![](https://stratpoint.com/wp-content/uploads/Chelsea-Logo-1.png "Chelsea Logo")

AWS Data Services

### Chelsea Logistics deploys applications for timely management reporting and self-service customer monitoring

![](https://stratpoint.com/wp-content/uploads/CL1.webp)

Background

Chelsea Logistics and Infrastructure Holdings Corp. (Chelsea Logistics) is the biggest shipping and logistics company in the Philippines. Serving the Philippine archipelago, it plays a crucial role in making sure goods and packages are delivered on time, passengers are safely transported to their desired destination, and businesses further their reach. Chelsea Logistics operates through its subsidiaries: Chelsea Shipping Corp.,Trans-Asia Shipping Lines, Inc., Worklink Services, Inc., Starlite Ferries, Inc., TASLI Services, Inc., and The SuperCat Fast Ferry Corporation.

Challenge

With around 100K transactions carried out in all of its operating arms, Chelsea Logistics needed management reports to keep track of its inventory, orders, and deliveries. They also wanted to create a view of the reports for monitoring transactions. All information was to be delivered via a self-service web portal.

Chelsea Logistics had a lean internal team of developers and AWS engineers. Through an AWS service provider, they planned to address the following challenges:

* Configuration and implementation of AWS QuickSight, to be embedded into their portal/frontend application to get information from external systems.
* Setup of AWS User Management (Permissions/Rights – Athena/Glue/S3).
* Setup of Google Drive Worksheet automatically transferred to AWS S3.
* Visualization of JSON via AWS QuickSight (SAAS App through API).
* Manual processing of up to 3,000 rows of data per day.
* Ability to align with AWS best practices to keep the system functioning optimally.

Solution

Stratpoint Technologies deployed an Agile team of data engineers and developers to work with Chelsea Logistics. The team aimed to configure AWS platform services into the Web Portal project according to best practices and to automate data extraction and transformation.
The following solutions were implemented to address the challenges experienced by the Client:

* The live data of the client was stored in JSON format and was fetched through an API. After fetching the data, the client manually stored it in AWS S3, which required a lot of work. The team automated data fetching from the API, then stored the data in AWS S3.
* The team implemented data cleansing to eliminate errors that were being encountered when using AWS platform services. It was discovered that data previously being loaded/ingested to AWS S3 had incorrect configurations, such as column whitespaces, special characters, and file encoding errors.
* After data cleansing, the team also implemented the best practices for using AWS platform services as a Data Lake, such as job bookmarking for AWS Glue jobs, proper delimiter for the AWS Glue crawlers, partitioning in AWS S3, and transformation to parquet file for compression and faster querying time in Amazon Athena.
* The team implemented the proper configuration and integration of AWS QuickSight with AWS Cognito for the dashboard embedding.
* The team was in constant communication with the client’s cloud team to provide the necessary permissions for AWS:

* IAM Roles & Policies (to be used for AWS S3, Glue, QuickSight)
* Lambda Configurations

![diagram1](https://stratpoint.com/wp-content/uploads/D1-980x716-1.webp)
Diagram 1 illustrates the architecture of automating of data fetching from the client’s API, which is then ingested into the AWS platform. 

1. EventBridge will trigger the Lambda function every minute.
2. Lambda function will fetch data from Yojee API with the interval of 1 minute. The data will be transformed, cleansed, and pushed to the S3 bucket.
3. Glue Crawler will be scheduled to run every hour to update the catalog.
4. Athena will be able to query the data in the S3 Bucket from the catalog.
5. QuickSight can use Athena as a medium for Dashboard Creation.

![diagram2](https://stratpoint.com/wp-content/uploads/D2-980x552-1.webp)
Diagram 2 illustrates the architecture focusing on using the AWS platform to centralize the client’s database and to automate the entire process of data transformation.

1. S3 Source bucket is configured with Event Notifications that whenever it detects a newly uploaded file, it will trigger the lambda function **S3ToCrawlerTrigger**.
2. Upon activation of the said Lambda function, the crawler **voyageformatcsv** will run. This will create or update a table with the schema of the uploaded file.
3. An EventBridge rule is then configured with an event pattern that when the state of **voyageformatcsv** changes to “succeeded”, it will trigger another Lambda function, **S3ToGlueTrigger**.
4. The **S3ToGlueTrigger** function will then trigger the glue job **strat-csv-catalog-to-parquet-partitioned-poc** to convert the newly uploaded file from csv to parquet.
5. The parquet file is automatically being cataloged by the glue job, which means it creates a table pointing to the S3 Target bucket where the parquet file is located.
6. Athena will then be able to query the parquet file using the generated table.
7. QuickSight can use Athena as a medium for Dashboard Creation.

Outcome

Upon project completion, Chelsea Logistics is now able to:

* Automate data fetching and data transformation, making the process faster, more efficient, and less prone to errors.
* Embed AWS QuickSight in its frontend application and use it to display reports, with data coming from multiple external sources.
* Use accurate and updated information to make management decisions in growing and improving business operations.
* Reduce costs by optimizing ETL pipelines (compression, file format conversion).

Technologies used

* React.JS
* Git Bash, Vim
* AWS Services: IAM, Athena, S3, Quicksight, Glue, Lambda, Cognito, EventBridge

## FEEDBACK

![](https://stratpoint.com/wp-content/uploads/Chelsea-Logistics-1.jpg)

#### Efren Bernardino Jr.

###### Chelsea Logistics | Sr. Information Technology Manager

|  |  |
| --- | --- |
| Having the right partner — Stratpoint — makes all the difference. | Adapting to new technologies is a challenge, but having the right partner makes the difference. With the help of Stratpoint, we successfully implemented a business intelligence tool, more easily and in an efficient way. The Group’s experience was both fun and educational. |

![](https://stratpoint.com/wp-content/uploads/Chelsea-Logistics-1.jpg)

#### Efren Bernardino Jr.

###### Chelsea Logistics | Sr. Information Technology Manager

|  |
| --- |
| Having the right partner — Stratpoint — makes all the difference. |
| Adapting to new technologies is a challenge, but having the right partner makes the difference. With the help of Stratpoint, we successfully implemented a business intelligence tool, more easily and in an efficient way. The Group’s experience was both fun and educational. |

![](https://stratpoint.com/wp-content/uploads/lets-connect-teal.png)
