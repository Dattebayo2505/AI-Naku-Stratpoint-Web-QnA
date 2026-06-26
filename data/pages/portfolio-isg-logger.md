---
url: https://stratpoint.com/portfolio-isg-logger/
title: Globe lays down the groundwork for predictive service operations with a centralized logger
crawled_at: 2026-06-26T11:20:30Z
lastmod: 2025-09-08T12:42:27+00:00
content_hash: sha256:d83264b8ec59bd28ee2776a21753dc0be321ba98a2b7a50a710f0979298fcedc
---
![](https://stratpoint.com/wp-content/uploads/Globe-icon-1.png "Globe-icon")

AWS Data Services

# Globe lays down the groundwork for predictive service operations with a centralized logger

![](https://stratpoint.com/wp-content/uploads/ISG-Logger-PV-Laptop.webp)

Background

Globe, a digital solutions platform and telecommunications provider in the Philippines, has more than 90 million customers, including mobile,  broadband, and landline users. They maintain managed private cloud servers, AWS servers, IT security elements, data center, and data center network devices.

Challenge

To maintain a high service level for their increasing customer base, Globe must transition to proactive and predictive service operations. To lay down the foundation for this forward-looking objective, Globe needs to establish a central hub of data for  IT operations.  This will serve as a repository for monitoring data and logs on their data center; a storage device for capturing logs coming from applications, databases, servers, and IT network components; and visualization/dashboarding tools to query and interpret data.

Solution

An Amazon Advanced Consulting Partner and data services provider, Stratpoint was onboarded to design and build Globe’s Central Data Logger.

* Platforms to be connected for data gathering were identified. The Central Data Logger was designed to communicate with key systems and to be data source agnostic, in order to collect data telemetry and logs from all environments.
* Splunk Forwarders collects data from both infrastructure and cloud servers, then sends the data via Splunk Data Stream Processors to an Amazon MSK cluster through a Kafka topic, buffering the data for 24 hours.
* Collected data is processed by Logstash sending output to both Amazon Opensearch and Amazon S3. Opensearch will serve as warm data for 7 days. Application logs stored here can be accessed in real-time through Kibana dashboard. S3 serves as the archive for part of the data sent by Logstash. The user or another application can also access historical (warm) data in S3 via SFTP protocol.
* Users can access the data stored in OpenSearch through a Cognito account assigned to them. They can then access Kibana to check the data and perform analytics, such as creating charts per metric.

Outcome

The Central Data Logger will enable Globe to:

* Store data for up to 2 years and query logs for up to 7 days
* Store historical data on their system monitoring at a more cost-effective rate in AWS S3
* Use open-source technology for data repository
* Eliminate volume/node-based subscriptions and utilize a more cost-effective solution
* Use visualization/dashboarding tools to query and interpret data
* Extract reports from Kibana
* Establish a central logger platform that is horizontally scalable, highly available, and can handle enterprise-wide data in the Cloud

As Central Data Logger collects more data, it provides Globe more insightful information in order to:

* Do faster issue resolution through impact analysis and predictive monitoring
* Forecast end-to-end capacity planning requirements
* Leverage data machine learning and artificial intelligence for anomaly detection, incident prediction, and autonomous repair response
* Potentially save 30% on monitoring license costs

Shannah Aberin, Globe’s project lead for the Centralized Logger initiative and part of Globe’s Service Operations Intelligence Center, reiterated the value of the solution in optimizing data management. “The Centralized Logger allows us to optimize and consolidate data management across multiple monitoring tools.”

Francis Pulmano, Globe’s Service Operations Intelligence Center Lead, says, 

“The Centralized Logger, which we have built with our Stratpoint partners, establishes a central hub for IT operations data, making it more accessible for support teams and stakeholders to leverage the data and enable proactive and predictive service operations. 

This initiative also provides a more cost-effective and efficient solution to collect and manage IT operations logs, giving us the flexibility to focus resources on maximizing data and providing insights.”

Technologies used

* AWS Services: EC2, MSK, Cloudformation, OpenSearch, Kibana, Lambda, Cognito, Cloudwatch, S3, SFTP, Logstash, Kinesis Data Streams, DynamoDB
* Splunk

## FEEDBACK

![](https://stratpoint.com/wp-content/uploads/globe-icon.png)

#### Francis Pulmano

###### Globe Telecom | Service Operations Intelligence Center Lead

|  |  |
| --- | --- |
| Stratpoint helped us make IT data accessible, so we can shift to proactive and predictive IT operations — all in a cost-effective solution. | The Centralized Logger, which we have built with our Stratpoint partners, establishes a central hub for IT operations data, making it more accessible for support teams and stakeholders to leverage the data and enable proactive and predictive service operations.This initiative also provides a more cost-effective and efficient solution to collect and manage IT operations logs, giving us the flexibility to focus resources on maximizing data and providing insights. |

![](https://stratpoint.com/wp-content/uploads/globe-icon.png)

#### Shannah Aberin

###### Globe Telecom | Project Lead, Centralized Logger Initiative Service Operations Intelligence Center

|  |  |
| --- | --- |
| Now we can optimize and consolidate our data. | The Centralized Logger allows us to optimize and consolidate data management across multiple monitoring tools. |

![](https://stratpoint.com/wp-content/uploads/globe-icon.png)

#### Francis Pulmano

###### Globe Telecom | Service Operations Intelligence Center Lead

|  |  |
| --- | --- |
| Stratpoint helped us make IT data accessible, so we can shift to proactive and predictive IT operations — all in a cost-effective solution. | The Centralized Logger, which we have built with our Stratpoint partners, establishes a central hub for IT operations data, making it more accessible for support teams and stakeholders to leverage the data and enable proactive and predictive service operations.This initiative also provides a more cost-effective and efficient solution to collect and manage IT operations logs, giving us the flexibility to focus resources on maximizing data and providing insights. |

![](https://stratpoint.com/wp-content/uploads/globe-icon.png)

#### Shannah Aberin

###### Globe Telecom | Project Lead, Centralized Logger Initiative Service Operations Intelligence Center

|  |  |
| --- | --- |
| Now we can optimize and consolidate our data. | The Centralized Logger allows us to optimize and consolidate data management across multiple monitoring tools. |

![](https://stratpoint.com/wp-content/uploads/globe-icon.png)

#### Francis Pulmano

###### Globe Telecom | Service Operations Intelligence Center Lead

|  |
| --- |
| Stratpoint helped us make IT data accessible, so we can shift to proactive and predictive IT operations — all in a cost-effective solution. |
| The Centralized Logger, which we have built with our Stratpoint partners, establishes a central hub for IT operations data, making it more accessible for support teams and stakeholders to leverage the data and enable proactive and predictive service operations. This initiative also provides a more cost-effective and efficient solution to collect and manage IT operations logs, giving us the flexibility to focus resources on maximizing data and providing insights. |

![](https://stratpoint.com/wp-content/uploads/globe-icon.png)

#### Shannah Aberin

###### Globe Telecom | Project Lead, Centralized Logger Initiative Service Operations Intelligence Center

|  |
| --- |
| Now we can optimize and consolidate our data. |
| The Centralized Logger allows us to optimize and consolidate data management across multiple monitoring tools. |

![](https://stratpoint.com/wp-content/uploads/globe-icon.png)

#### Francis Pulmano

###### Globe Telecom | Service Operations Intelligence Center Lead

|  |
| --- |
| Stratpoint helped us make IT data accessible, so we can shift to proactive and predictive IT operations — all in a cost-effective solution. |
| The Centralized Logger, which we have built with our Stratpoint partners, establishes a central hub for IT operations data, making it more accessible for support teams and stakeholders to leverage the data and enable proactive and predictive service operations. This initiative also provides a more cost-effective and efficient solution to collect and manage IT operations logs, giving us the flexibility to focus resources on maximizing data and providing insights. |

![](https://stratpoint.com/wp-content/uploads/globe-icon.png)

#### Shannah Aberin

###### Globe Telecom | Project Lead, Centralized Logger Initiative Service Operations Intelligence Center

|  |
| --- |
| Now we can optimize and consolidate our data. |
| The Centralized Logger allows us to optimize and consolidate data management across multiple monitoring tools. |

![](https://stratpoint.com/wp-content/uploads/lets-connect-blue.png)
