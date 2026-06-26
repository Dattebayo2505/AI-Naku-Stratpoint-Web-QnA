---
url: https://stratpoint.com/2021/04/30/finding-flutter-our-cross-platform-journey/
title: Finding Flutter: Our Cross-Platform Journey | Stratpoint Blog
crawled_at: 2026-06-26T11:20:30Z
lastmod: 2025-06-19T08:47:07+00:00
content_hash: sha256:de0d47a7e12fa4459bd2043c6d2e5c4dce6ad8c0709d2671fdedf21c8bb22312
---
![](https://stratpoint.com/wp-content/uploads/Finding-flutter.webp "Finding-flutter")



#### April 30, 2021

[Software Services](https://stratpoint.com/./services/software-services/)

Finding Flutter: Our Cross-Platform Journey

*This article is by Stratpoint Mobile Group Head Paolo Alcabao, based on his session entiled “Finding Flutter: Our Cross-Platform Journey” in Flutter Engage: Extended Philippines on March 31, 2021.*

Humans are always in search of something faster and better, especially when it comes to technology. It’s a never ending search for the Holy Grail, a silver bullet. While we are speaking in the language of high fantasy, to me as a developer, the Holy Grail can be having a machine (or a genie) that builds whatever features I wish through verbal command. But for now, I just want something that can help make the work of my team easier and improve the quality of our output.  
 

### The struggle is real

Application development is complex. Users demand customization, trendy aesthetics, intuitive design, and more. They have multiple devices, but they want to have the same seamless experience from one to another.

Behind the scenes, development teams labor day and night to deliver these demands. We may have separate teams for iOS, Android, and web. We are always chasing a launch date. More often than we like, we have to take over the work of another developer, whose approach to problem solving is totally different from our own. We can either spend hours trying to decode code, or start the work all over again. Either way, it’s unproductive, and, frankly, exhausting.

As a technology company, Stratpoint recognizes that a simple, cross-platform approach will give us a competitive advantage. Develop once, and deploy to multiple channels. Streamline the team’s skill set. Manage timeline and cost while maintaining the high quality of our work.  
 

### The promise of cross-platform

We pursued the idea of cross-platform because it offers all three things we were looking for: speed, cost, and quality. 

There is only one development timeline because we don’t have to build and maintain separate code bases. We don’t have to add resources so that we can cover multiple operating systems. With cross-platform, we can be fast. We can be lean.

We follow the concept of “good enough” — which is not to say that we are compromising quality. For example, the value of reducing API response time from 0.4 seconds to 0.1 seconds may not commensurate with the additional 3 weeks needed to implement the optimization for a particular device. But we are still providing an app that works sufficiently fast for all devices and that we can deploy ahead of the competitor.  We strike a balance between quality and value.

Cross-platform works, and now we have to find the right tool.  
 

### The search for the right weapon

We started in 2014 with Cordova, its paid version Phonegap, and Ionic that builds on the open source Cordova. These technologies display code as web pages. The web-based implementation approach worked. We had a single code base that we deployed to both iOS and Android, helping manage cost and slightly shortening the project timeline. 

The most glaring issue we encountered in our Cordova projects was when the apps did not behave the same way in iOS and in Android. This was an expected and known incompatibility issue between the web engines and the mobile devices. Also, some of the niceties in the native iOS or Android cannot be replicated well or within reasonable time using the web-based implementation approach. 

Another path we pursued was integrated platform solutions, IBM MobileFirst in particular. This approach entails buying into the ecosystem — all its perks, advantages, and weaknesses.

Initial setup was a challenge. To get started with even just one app, we had to configure the platform as a whole, including the application center, the hosting server, etc. After that, MobileFirst worked really well: from deployment, development, application management, user management, and integration with other applications in the same platform. Everything was smooth-sailing as long as we fit into the platform’s environment.

We encountered issues when we attempted to work outside of the system, like when we wanted to replace the deployment server with AWS or integrate with other cloud platforms to access already available functionalities. Within the MobileFirst ecosystem, it was all good, and we even over-delivered on speed and performance. But in real-world application, it is rare that a client is fully invested into one system only. In this regard, IBM MobileFirst was not flexible at all.

Next up, we found the bridges or intermediaries, namely, React, Xamarin, and Flutter. The complicated architecture/approach of the first two caused us some difficulties in debugging and performance (React has recently amended this). 

In 2018, we found Flutter, the open-source UI software development kit created by Google. Flutter, in my opinion, is as good as it gets as a cross-platform tool, without being native. With a straightforward architecture, Flutter definitely delivers on performance and in having apps behave the same way between iOS and Android. 

On the flip side, it has its compromises too. Unlike React, Flutter does not invoke native components of mobile devices. If, for example, iOS makes major changes to their look and feel, Flutter will not necessarily follow suit. This happened when Apple transitioned from iOS 6 with the bezels and shadows aesthetics to iOS 7 with the flat design. In such cases, Flutter apps would maintain the old design, and we will have to wait for updates to be able to apply the new design.   
 

### Wielding Flutter

We embraced Flutter’s qualities, its pros and its cons. After dabbling in so many tools, we have now chosen Flutter as our go-to cross-platform weapon in our development armory. Here’s why.

*DevOps-ready*. Flutter, just like Stratpoint, is DevOps-ready. Our clients and most of the IT industry are either already practicing or transitioning to DevOps, and so it is important that our tools be easily adaptable into any situation and environment. By default, Flutter has rich documentation, works with Gitlab, has an easy-to-use command line interface, and has its own testing libraries. 

*Leverage Pipeline as Code.* With Flutter and Gitlab, we leverage Pipeline as Code, making it easier for us to maintain templates, track who made changes and what, do configurations, and have better accountability. 

*Easy-to-use Command Line Interface*. Flutter’s CLI has a vast library of useful commands: to create an app, to test, analyze, and generate packages, to check your installation, among many others. If all else fails, you can go back to your command lines and bridge gaps. You can even create your pipeline through skips alone, if necessary.   
 

### More and more brands are using Flutter

My BMW is the luxury car manufacturer’s iOS and Android app that acts as the universal interface with a customer’s car, providing round-the-clock information on the vehicle’s status. It can remotely locate the vehicle, lock and unlock doors, and monitor the vicinity of the vehicle. It integrates with mobile devices and Alexa too. Because My BMW’s Flutter architecture is scalable and future-proof, it can easily expand in scope, add more features, deploy improvements, and ultimately, cater to the demands of its market.

Other popular brands that use Flutter in their apps are Grab Food, Ebay, Alibaba, and Groupon. Meanwhile, Google is working on using Flutter to drive the overall system UI of Google Assistant’s Smart Display.  
 

### Our adventure with Flutter continues

The battle to deploy apps fast and provide the best user experience is raging. We find that Flutter is a reliable and promising weapon in which to invest our time and resources. In return, we expect to continue enjoying the holy trinity of IT: speed, cost, and quality.

Have we found our silver bullet? Probably not. Like many technologies, Flutter has its strengths and weaknesses, but it does a great job for many of our requirements (and BMW’s, Grab’s, and Google’s) and a good enough job for a few exceptional needs. 

Finally, with the release of Flutter 2.0, we are excited to explore new cross-platform features… and powers. 

Explore how you too can leverage the power of Flutter as a cross-platform tool. Email us at [hello@stratpoint.com](mailto:hello@stratpoint.com).

Related Blogs

[View More](https://stratpoint.com/blogs?tab=ss)

╳

![]()
