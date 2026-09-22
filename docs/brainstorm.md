# What?
The point of backbone is to be able to view infrastructure in more detail, including its effects on people.

# Why?
So much infrastructure sucks, and what makes infrastructure is very nebulous. There are so many factors that go into a walking path that make it good/bad, for example: noise from nearby highways, pollution, shade coverage, number of amenities, connectivity to the rest of the network, etc. These are hard to all take into account and understand, so you need a single place to figure out what factors are good and bad.

# Where?
I think the past place to serve an application like this is from the web, since that will make it most accessible and won't require anyone to download anything.

# How?
One of the trickier parts. We need to:

1. Aggregate the existing infrastructure to such a degree that it is easily viewable and recognizable by a user, and also detailed enough that insights can be extracted

2. Present this infrastructure in some kind of UI

3. Be able to run certain "tests" or "modules" on the infrastructure to view how it performs according to a variety of metrics

4. Be able to modify the infrastructure, and re-run tests on it after modification
