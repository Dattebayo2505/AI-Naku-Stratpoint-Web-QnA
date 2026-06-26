---
url: https://stratpoint.com/2020/09/29/solaire-resort-and-casino-managed-services/
title: Solaire Resort & Casino Managed Services
crawled_at: 2026-06-26T15:37:11Z
lastmod: 2022-03-07T04:42:19+00:00
content_hash: sha256:97da4646ca820a998ca426f9fa596014e1a085070aca81270f54fc895fff4426
---
# Solaire Resort & Casino Managed Services

![sample-blog](https://stratpoint.com/wp-content/uploads/sample-blog-image-scaled.jpg "Young men testing virtual reality technology")

### **About**

Solaire Resort & Casino is a high class resort and casino situated in Manila’s Entertainment City. The establishment boasts in it’s impressive number of accommodations, exceptional gaming facilities, extensive dining options, and state-of-the-art lyric theatre, offering a unique experience of comfort, elegance and luxury to its customers. Potential clients and customers would conduct their booking and reservation activities on the establishment’s website or through the Apple  and Android applications. The establishment opened in 2013, with the complex featuring a total of 800 rooms, suites, and villas. Solaire also features a column-free grand ballroom which can accommodate a maximum of 1,300 guests, including a gaming area containing 1,620 slot machines and 360 gaming tables.

### **Challenge**

As the establishment continues to thrive and develop in its respective industry, there is a pressing need for the current existing online infrastructure in shifting to a more efficient cloud solution in order to accommodate the increasing number of its daily traffic and new users monthly. In addition, the establishment is also faced with the technical concern that its infrastructure is entirely reliant on a single server, leaving reliability, availability, and fault tolerance at risk should the system fail. The website’s maintenance, infrastructure, and networking are also being handled by different services, making management a hassle. Therefore, the establishment seeks for a better cloud-based solution that incorporates the best practices of scalability, agility, elasticity, and innovation through its linked cloud services.

### **Why Amazon Web Services**

Solaire Resort & Casino has grown substantially in the past 6 years. So, in order to keep up with the high level of demand from its new and existing customers, the company has migrated it’s entire infrastructure over to the AWS cloud.

The establishment’s applications are powered by Amazon Elastic Cloud Compute (Amazon EC2) instances to provide online services that would be running at maximum durability and reliability. To combat the potential daily traffic of the business’s users, Solaire used Elastic Load Balancing to evenly distribute and efficiently handle incoming traffic between multiple Amazon EC2 instances. Amazon Cloudwatch and AWS Lambda would be working close together to ensure that the system would have consistent backups. These data backups would then be stored on Amazon EC2 instances with mounted Amazon Elastic File Systems (Amazon EFS). An Amazon Simple Notification Service (Amazon SNS) is then used to notify users that the backups were either successful or failed. Amazon Virtual Private Cloud (Amazon VPC) was used to ensure security between network traffic sent between the application and the users.

A database layer in Amazon RDS (Relational Database Service) using MySQL 5.5 was used to simplify database administration. The relational database would be easy to set up, operate, and scale thanks to Amazon RDS, which provides cost-efficient and resizable capacity while automating time-consuming administration tasks such as hardware provisioning, database setup, patching, and backups. Amazon RDS can be scaled vertically by upgrading to a larger Amazon RDS DB or adding more and faster storage. It can also be scaled horizontally for read-heavy applications.

With the exception of its DNS hosting services handled by Cloudflare, the system’s entire infrastructure was successfully migrated over the Amazon Web Services cloud.

### **The Benefits**

After its successful migration, Solaire Resort & Casino felt significant positive changes from its new infrastructure, as its online services were able to scale in and out effectively to the ongoing traffic from the establishment’s total users, supported by network load management services such as AWS Elastic Load Balancing and AWS Auto Scaling.

The company was able to take full advantage of the AWS cloud capabilities, whereas Solaire Resort and Casino formerly had to rely on fixed resources from another cloud computing service. Now, they are able to dynamically allocate resources and pay only for what the company uses.

Thanks to Amazon RDS Multi-AZ deployment feature, high availability is assured as data stored in the system’s database is automatically backed up to spare database instances  in the event of a system failure. 

Solaire was able to save an estimated 7% of TCO savings from migrating to AWS from it’s previous infrastructure solution. The company saved a tentative amount of $33.21 from it’s monthly charge of $507.03 to $473.82. 

In addition to the cost reductions  from migrating to AWS cloud, Solaire Resort & Casino only has to be concerned of their own workload applications thanks to AWS Shared Security Responsibility model. This means that maintenance related and security related tasks such as patches are automatically applied to the user’s configuration settings, reducing operational overhead for the company and exposure to vulnerabilities. Solaire would only need to be concerned with their application’s security while Amazon handles the security of the cloud infrastructure.



#### September 29, 2020



#### [Case Studies](https://stratpoint.com/./case-studies/)

## Blogs

[Next: Serverless using AWS Lambda →](https://stratpoint.com/2020/09/30/serverless-using-aws-lambda/)

╳

![]()
