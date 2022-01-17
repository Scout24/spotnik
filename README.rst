.. image:: https://travis-ci.org/ImmobilienScout24/spotnik.png?branch=master
   :alt: Travis build status image
   :align: left
   :target: https://travis-ci.org/ImmobilienScout24/spotnik

.. image:: https://landscape.io/github/ImmobilienScout24/spotnik/master/landscape.svg?style=flat
   :target: https://landscape.io/github/ImmobilienScout24/spotnik/master
   :alt: Code Health

=======================================================
This project is DEPRECATED and not any longer supported
=======================================================


Spotnik
=========
Spotnik is a tool for users of AWS (Amazon Web Services) to save money on their EC2 instances. This is done by gradually replacing the regular on-demand instances of an auto scaling group (ASG) with `spot instances <https://aws.amazon.com/ec2/spot/>`_. Most types of spot instances cost 80-90% less than their on-demand counter parts, so considerable savings are possible. Spotnik was greatly inspired by `autospotting <https://github.com/cristim/autospotting>`_ and implements the same basic idea, albeit in a different way.

On the following page you can see the possible Spot Instance types and the current prices:  `Spot Instances Pricing <https://aws.amazon.com/ec2/spot/pricing/>`_

In comparison, the on-demand costs: `www.ec2instances.info <http://www.ec2instances.info/?region=eu-west-1>`_


## Spotnik is currently under development and not yet ready for production use. ##

How does it work?
=================
Overview
--------
`Autoscaling groups in AWS <https://aws.amazon.com/autoscaling/>`_ do a lot of useful things for you, like scaling a cluster up and down as needed or evenly distributing your instances over various availability zones. A lesser known feature of ASGs is that they allow you to add and remove instances via an API. Spotnik uses this to add cheap (spot) instances to an ASG and then remove expensive (on-demand) instances.

With the spot instances becoming members of the ASG, they are also under the ASGs control: ASG operations like scaling up/down or updating your application to a new version continue to work, since the ASG is aware of the spot instances and can terminate them as needed. Since Spotnik only extends the functionality of the ASG, the ASG remains fully functional even if Spotnik is deactivated.

Technical Details
-----------------
Spotnik runs as a Lambda Function that is triggered every few minutes. It searches all regions for ASGs that have the tag "spotnik", regardless of the tag's value. If the ASG has some on-demand instances, Spotnik will try to replace one of those instances with a spot instance. This replacement is not carried out in a single run of the Lambda Function, it requires two runs of the Lambda:

* In the first run, the Lambda function requests a spot instance.
* Once that spot request has been fullfilled, a subsequent run of the Lambda function attaches the new instance to the ASG. Then it detaches the old instance.

To associate pending spot requests with an ASG, the spot requests are tagged with the names of both the ASG and the instance that will be replaced. In order to easily determine which spot instance is already attached, the tag with the ASG's name is removed once the new instance was attached to the ASG.

Internally, Spotnik is divided into two classes that handle the two main concerns of Spotnik. The class ReplacementPolicy decides whether on-demand instances of an ASG are replaced at all. It also decides which instance to replace and what the replacement should look like (launch configuration, bid price). The class Spotnik then carries out the decision that was made by the ReplacementPolicy. This design was chosen to make it easy to implement new replacement strategies, since the logic is in one place. It will also make it possible to use different (and configurable) replacement policies per ASG.

Spotnik's Lambda function concurrently handles all ASGs in all regions. The main thread launches a worker thread for each region. Each regional thread then launches a thread for each Spotnik-enabled ASG in the region, which then does the actual work. This means that many threads are running concurrently. Python's GIL is not a problem, though, since the threads spend most of their time waiting (due to network latency and not-so-fast AWS APIs).

How do I use it?
================
Two things are needed to get Spotnik running in an AWS account. First, you need to deploy the Spotnik stack, which mainly contains a Lambda function and an IAM role. If (and how) a certain ASG is handled by Spotnik is determined by the ASG's tags. So the second task is to apply tags to the ASG.

Deploy the Spotnik Stack
------------------------

Login to your AWS Console and click on the link below to deploy the required CloudFormation stack.

 `Launch spotnik <https://console.aws.amazon.com/cloudformation/home?region=eu-west-1#/stacks/new?stackName=spotnik&templateURL=https://s3-eu-west-1.amazonaws.com/spotnik-distribution/spotnik-lambda.json>`_

OR

If you prefer `awscli <http://docs.aws.amazon.com/cli/latest/userguide/cli-chap-welcome.html>`_ to deploy spotnik in your account (you must have credentials):

::

    aws cloudformation create-stack \
    --stack-name spotnik \
    --template-url https://s3-eu-west-1.amazonaws.com/spotnik-distribution/spotnik-lambda.json \
    --capabilities CAPABILITY_IAM \
    --parameters ParameterKey=codeDistributionBucketName,ParameterValue=spotnik-distribution ParameterKey=spotnikZip,ParameterValue=latest/spotnik.zip ParameterKey=ScheduleExpressionCron,ParameterValue='cron(0/2 * * * ? *)'


OR

You could also download the CloudFormation template and change it according to your wishes:

::

    aws s3 cp s3://spotnik-distribution/spotnik-lambda.json .

To deploy the local CloudFormation template use the option "--template-body" instead of --template-url:

::

    aws cloudformation create-stack \
    --stack-name spotnik \
    --template-body ./spotnik-lambda.json \
    --capabilities CAPABILITY_IAM \
    --parameters ParameterKey=codeDistributionBucketName,ParameterValue=spotnik-distribution ParameterKey=spotnikZip,ParameterValue=latest/spotnik.zip ParameterKey=ScheduleExpressionCron,ParameterValue='cron(0/2 * * * ? *)'


Apply Tags to the ASG
---------------------
Spotnik understands the following tags on ASGs:

* **spotnik**: Regardless of the tag's value, every ASG with this tag will be handled by Spotnik
* **spotnik-bid-price**: How much to bid for each spot instance (US$ per hour). Required parameter.
* **spotnik-instance-type**: Which instance type(s) to use for spot-requests, e.g. "m4.large, c4.large". Defaults to the instance type of the replaced instance. Check `Spot Instances Pricing <https://aws.amazon.com/ec2/spot/pricing/>`_ to see which Instance types are configurable.
* **spotnik-min-on-demand-instances**: How many on-demand instances Spotnik should leave in the ASG. Defaults to 0.

  - Keep in mind that a scale down of the cluster may remove the on-demand instances, depending on the ASG's Termination Policy.
