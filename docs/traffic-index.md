# The what now?
I'm proposing a traffic metric (the Traffic Index) to evaluate the quality of infrastructure. Basically, I'm making the assumption that a piece of infrastructure is "good" if it is highly trafficked, and "bad" if it is not. This is a rather simplistic assumption (and a bit utilitarian) but I'm sure there'll be plenty of time for subtlety later. I'm not judging the desirability of a place necessarily at this stage. We might also have goals related to the preservation of natural space or metrics like lower crime on our "piece of infrastructure." We'll worry about it later. Right now, we care only about traffic, because a piece of infrastructure is most "efficient" (from a tax-dollars perspective) if it gets more use. If a government agency was trying to maximize the value per dollar, this is where the "value" calculation could plausibly be partially derived from. 

Now, the question is, how might we calculate how much traffic is going to pass through a certain piece of road/path?

# Connectivity
Let's ignore loop trips (trips with the same origin and destination) for now. Every purpose has a destination, and it can be assumed that some characteristics about this destination caused the trip to happen in the first place. And the origin must have a person located there for the trip to happen in the first place, else there'd be no one to send to the destination!

Let's greatly simplify the problem greatly, and consider an example where everyone is currently at home, and is looking to go to the grocery store.

If the grocery store is the (D)estination and the O(rigin) represents all of the houses in this neighborhood, then for this point * where we want to measure traffic, we need only know the number of people at the origin. If every person operates independently, the number of trips across * scales linearly with the number at O.

`O-*-D`

Now let's assume we had a second grocery store, like this:

```
O-*-D
|
D
```

Assuming that these were equidistant from these homes, we would expect half of the trips to go to each of the stores.

This becomes more complicated if the distance between O and each of the Ds is different. That's where we have to introduce logsums:

$P_{ij} = \frac{\exp(V_{ij})}{\sum_{k}\exp(V_{ik})}$

Where $V_{ij}$ (the utility from i to j) are weighted costs $U_{ij} - \beta_0*d_{ij}+\beta_1*c_{ij}+...$, where $d$ might stand for distance, $c$ for comfort, etc. These weights will need to be tuned, because we don't actually know how much people actually care about distance and comfort, and how it might influence their decision to pick another store.

So we might expect

$\verb|population|(O)*\frac{\exp(-d_{od1})}{\exp(-d_{od1})+\exp(-d_{od2})}$

across *. 

# Trips
Traffic along a certain corridor equals the number of trips which make use of that corridor. There are a very large number of reasons people might make a trip. It's interesting to think about how one might categorize them. The status quo includes something like the list below (taken from [NHTS](https://nhts.ornl.gov/) data):

1. Regular home activities (chores, sleep)
2. Work from home (paid)
3. Work
4. Work-related meeting / trip
5. Volunteer activities (not paid)
6. Drop off /pick up someone
7. Change type of transportation
8. Attend school as a student
9. Attend child care
10. Attend adult care
11. Buy goods (groceries, clothes, appliances, gas)
12. Buy services (dry cleaners, banking, service a car, pet care)
13. Buy meals (go out for a meal, snack, carry-out)
14. Other general errands (post office, library)
15. Recreational activities (visit parks, movies, bars, museums)
16. Exercise (go for a jog, walk, walk the dog, go to the gym)
17. Visit friends or relatives
18. Health care visit (medical, dental, therapy)
19. Religious or other community activities
