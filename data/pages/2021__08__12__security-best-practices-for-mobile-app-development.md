---
url: https://stratpoint.com/2021/08/12/security-best-practices-for-mobile-app-development/
title: Follow best practices specific to the OS
crawled_at: 2026-06-26T11:20:30Z
lastmod: 2025-06-20T02:18:40+00:00
content_hash: sha256:95c3172f95b313bea8442b87cd187ac2c2505e3f4204acffd16e647b57b646c8
---
![](https://stratpoint.com/wp-content/uploads/Security-best-practices-for-mobile-app-development.webp "Security best practices for mobile app development")



#### August 12, 2021

[Software Services](https://stratpoint.com/./services/software-services/)

Security best practices for mobile app development

In 2020, [Kaspersky](https://go.kaspersky.com/rs/802-IJN-240/images/KSB_statistics_2020_en.pdf) placed the Philippines among  the Top 20 countries where users face the most risk of online infections. It was also [reported](https://securelist.com/mobile-malware-evolution-2020/101029/) that malicious files have recently taken on a pandemic theme, containing the name “covid” in their names to trick users into trusting and downloading them. Hackers are always getting smarter, more experienced.

Most mobile application development teams would already have security best practices in place, including encryption, documentation, and Open Web Application Security Project (OWASP) guidelines. Still, it’s important to remind and update ourselves to be ready for new kinds of attacks. In this blog post, we listed security best practices for common mobile app vulnerabilities.

# **Follow best practices specific to the OS**

If you use a fingerprint reader instead of the operating system’s fingerprint scanner security framework, you may expose user credentials to a third party. You risk both the private information of your subscriber and their loyalty. Multiply the risk by the number of users you have, and you’ve got yourself a massive breach.

It’s hard to detect improper platform usage post-development because the definition of improper usage is broad. You may use tools like SonarQube to scan build files for known vulnerabilities and risks. Avoid this by referring to mobile app security best practices specific to each platform your app is in. Apple and Google have extensive and widely available documentation.

## **Handle PII with caution**

When an attacker is wily enough to get hold of a device physically or virtually, they can use free software to access anything in the device, including personally identifiable information (PII) such as addresses, birthdays, and ID numbers.

Protect your user. As much as possible, do not instruct your app to store PIIs in the device. However, if it is inevitable, encrypt. Take inventory of all the information your application stores or transmits, know where and how the data is stored or transmitted, and follow  best practices in relation to the risks associated with these data types. 

## **Secure communications in and out of the app**

In the course of getting information from an app server to the user device, attackers may be listening in through the common wifi. Even if your app sends encrypted user credentials to authenticate a token, you may be sending back plaintext in the next API calls, something hackers can use to wreak havoc. 

The solution — always use security protocols such as SSL/TLS, as well as related techniques such as Certificate Pinning 

## **Protect your app from code tampering**

Once a user downloads your app into their device, you have no control over the environment it is in. For example, they can insert code into your premium gaming app that captures your customer’s username and password and sends it to another location. They will then distribute the malicious version of the app in third-party libraries or by tricking customers into clicking a link in an email to get your app for free. This endangers not only your customers’ data but also your brand reputation.

All apps are vulnerable to code tampering. To prevent this scenario, when your mobile app runs, it must be able to tell that its code has been changed. Because unauthorized apps usually work on jailbroken devices,  one way to protect your app is to detect whether certain applications or libraries (commonly associated with jailbroken devices) are present in the environment it is installed in. If they are, you can instruct your app to shut down. Both iOS and Android have tools that can help.

## **Are your apps secure?**

Stratpoint incorporates mobile application security best practices from conceptualization, design, implementation, to support of all our software. If you want to make sure your new app works great *and* protects your users, send us a message through the form below.

Related Blogs

[View More](https://stratpoint.com/blogs?tab=ss)

╳

![]()
