# Jalani Control Tower: Problem and Solution

*Jalani (জ্বালানি) means "fuel". This page explains the problem and our solution in simple words. It is written for the round 1 discussion.*

---

## 1. The problem in one paragraph

A fuel network moves fuel from depots to stations, and from stations to customers. In our simulated Bangladesh network, stations hold **less than one day of fuel**. If nobody sends new fuel, they run dry. Supply from outside is limited and stops after about two days. Things also go wrong: demand jumps, roads close, shipments arrive late, and software fails.

**The operations team needs a system that helps them decide which fuel to send where, and when. It must keep working when parts of it break.**

---

## 2. The simulated world

The organizers give every team the same simulator. It is the "world", not the "brain": it only executes our shipment orders and reports what happened.

```
                DHAKA DIVISION
  Gazipur Depot ──────────────▶ Mirpur station       (2 ticks, max 7,000 L per shipment)
  (sends 12,000 L per tick) ──▶ Tongi station        (2 ticks, max 6,500 L)  ← ONLY route to Tongi
                  └─────────────▶ Karnaphuli station (4 ticks, cross-region backup)

                CHATTOGRAM DIVISION
  Patiya Depot  ──────────────▶ Karnaphuli station   (2 ticks, max 7,000 L)
  (sends 11,000 L per tick) ──▶ Cox's Bazar station  (3 ticks, max 6,000 L)  ← ONLY route to Cox's Bazar
                  └─────────────▶ Mirpur station     (4 ticks, cross-region backup)
```

- There are **3 fuels**: Diesel, Petrol and Octane.
- **1 tick = 15 simulated minutes.** At the default speed, the simulator runs 8 ticks per second, so **one simulated day passes in 12 real seconds.**
- There is only one way to act on the world: `POST /v1/allocations`, which sends one shipment.

---

## 3. What we measured

We did not only read the documents. We ran the official simulator ourselves and tested it.

### 3.1 If nobody acts, the network collapses

| End of day | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| Customers served (service level) | 88% | 46% | 31% | 23% | 19% | **15%** |

### 3.2 Other key numbers

- **Demand:** customers need about **93,000 litres per day** in total.
- **Supply ends early:** 22 supply deliveries arrive (216,000 L in total). The last one arrives on **day 2.2**, and after that no new fuel comes. With perfect play, the network lasts about 6 days.
- **Chattogram runs short of petrol first.** It has about **4.2 days** of petrol left, while Dhaka has **7.2 days**. So fuel must move from Dhaka to Chattogram.
- **Weak spots:** Tongi and Cox's Bazar each have **only one road**. If that road closes, nothing can reach them.

### 3.3 Three hidden traps (not in the documents)

We found these by testing. Each one silently destroys fuel.

| Trap | What happens | What we measured |
|---|---|---|
| 1. Full depot | New supply that doesn't fit in a full depot is thrown away | **73,000 L lost** if nobody acts |
| 2. Full station | A shipment that doesn't fit in a station's tank is partly thrown away | 3,882 L lost in one test |
| 3. Road closes at the wrong moment | If a road closes on the same tick a shipment leaves, the shipment fails and **its fuel is never returned** | 2,000 L lost in one test |

A good fix exists for trap 3: **cancelling** a shipment before it leaves returns all of its fuel.

### 3.4 One big advantage

The simulator is **deterministic**: the same actions always give exactly the same result. We tested this. It lets us compare different decision methods fairly, on exactly the same situation.

---

## 4. The real problems to solve

1. **Predict:** know which station will run out, when, and how sure we are.
2. **Decide:** choose how much fuel to send, from which depot, on which road, while respecting all limits (stock, tank size, truck size, depot sending limit, closed roads).
3. **Avoid waste:** never lose fuel to the three traps.
4. **Share fairly:** after supply stops, there isn't enough for everyone, so shortages must be spread fairly.
5. **React to surprises:** demand spikes, road closures, station outages, late or smaller supply.
6. **Keep humans in control:** time moves fast (a day every 12 s), so a person can't approve every truck. They should approve the important decisions.
7. **Survive failures:** the simulator API can become slow, return errors, go down, send old data, or drop its live stream. Our own services can fail too.
8. **Prove it:** show numbers, not claims.

---

## 5. Our solution: Jalani Control Tower

Jalani is an **operations center**: a web app with a smart backend. It follows the brief's loop:

**Observe → Detect → Predict → Decide → Simulate → Act → Monitor → Recover**

| Step | What Jalani does, in simple words |
|---|---|
| **Observe** | Reads the whole network from the simulator every tick. Checks that the data makes sense before using it. |
| **Detect** | Notices changes: demand spikes, road closures, station outages, late or smaller supply, and any fuel that was lost. |
| **Predict** | Forecasts demand for every station and fuel. Calculates **hours until empty** and the **chance of running out**. |
| **Decide** | An optimizer (a linear program) plans shipments for the next 12 hours while respecting every simulator rule. |
| **Simulate** | Shows the expected result before acting, e.g. "risk of running out: 99% → 7%". |
| **Act** | Sends shipments safely. A retry never creates a duplicate shipment. |
| **Monitor** | Dashboards show the health of the network and of our own software. |
| **Recover** | When something fails, the system moves to a safer mode and recovers automatically. |

### 5.1 How it predicts

- **Demand forecast:** each station has a daily pattern (busy hours and quiet hours). We learn that pattern and adjust it to recent demand. When the simulator reports a demand spike, the forecast updates immediately.
- **Uncertainty:** we measure how wrong our forecast usually is, and use that to calculate a **confidence level**.
- **Risk:** for every station and fuel we show hours until empty, chance of running out in the next 8 hours, and a risk level (OK, Watch, High, Critical). If no truck can arrive in time, we say so honestly ("unavoidable, reduce impact").

### 5.2 How it decides

- **Optimizer (main method):** a linear program. It looks ahead, sees scheduled supply and scheduled road closures, and plans shipments to:
  - reduce unmet demand, giving higher priority to important stations;
  - avoid all three fuel traps;
  - keep a safety buffer that depends on how uncertain the forecast is;
  - spread shortages fairly when fuel is scarce.
- **Backup method:** a simple rule ("refill the station that will run out first, from the best depot"). It runs if the optimizer fails. It is also our comparison baseline.
- **Shipment guard:** if a road is about to close, Jalani never sends a truck on it, and it **cancels** waiting shipments so their fuel is returned.

### 5.3 Humans stay in control

| Type of decision | Who decides (default mode) |
|---|---|
| Routine refill inside a region, high confidence | Automatic |
| Moving fuel between Dhaka and Chattogram | **Operator approval** |
| Rationing when fuel is scarce | **Operator approval** |
| Low confidence or old data | **Operator approval** |
| Safety actions (cancel a shipment on a closing road) | Automatic |

- Each decision waiting for approval shows the **reasons**, the **expected effect**, the **confidence**, **other options**, and a **deadline**: the last moment when the shipment can still help.
- When the operator approves, the decision is checked again against fresh data before it is sent.
- There are three modes: **Advisory** (a human approves everything), **Supervised** (the default), and **Autopilot**.
- Every decision is saved in a history with who approved it and what happened.

### 5.4 How it stays up when things break

| What goes wrong | What Jalani does |
|---|---|
| Simulator is down | Goes to **SAFE HOLD**: shows the last known state and an estimate, and sends nothing until the simulator is back |
| Simulator returns random errors | Retries safely. The same shipment ID means no duplicates |
| Simulator is slow | Timeouts and a circuit breaker; the dashboard stays fast using the last data |
| Simulator sends old data | Goes to **DEGRADED**: automation pauses and a human must approve |
| Live stream is cut | Switches to regular polling automatically |
| Our forecasting/optimizer service is down | Switches to the backup rule automatically (**FALLBACK** mode) |
| Bad or strange data | Rejects it, raises an alert, keeps the last good data |
| Simulator is reset by someone | Detects it, reloads the world and starts fresh |

---

## 6. How we prove it works

1. **Fair benchmark.** We run a second copy of the official simulator and replay the same situation with three methods: *do nothing*, *simple rule*, *our optimizer*. Then we compare customers served, fuel lost and failed shipments.
2. **Live dashboards.** Prometheus and Grafana show request speed, errors, CPU and memory, forecast error, risk levels, decisions made, and how often the backup mode was used.
3. **Load test.** k6 sends many requests to our API and measures speed: average, p50, p95, p99, requests per second and error rate.
4. **Live failure demo.** We break things on purpose in front of the judges and show the recovery.

---

## 7. What we left out, and why

- **Reinforcement learning:** too slow to train here. The simulator runs about 32 steps per second, so 1 million training steps would take about 9 hours. The network is small and fully visible, so an optimizer is the better, explainable tool.
- **Kubernetes:** one server and one simulated world don't need it. Docker Compose is simpler and reliable. Our services are ready for Kubernetes later.
- **A chatbot:** GPT only writes explanations and incident summaries from our computed facts. It never makes decisions.

---

## 8. Technology

| Part | Technology |
|---|---|
| Backend | Python, FastAPI |
| Forecast and optimizer service | Python, FastAPI, SciPy (HiGHS linear programming) |
| Web app | React, Vite, TypeScript |
| Data | SQLite (decision history and audit log) |
| Monitoring | Prometheus, Grafana, cAdvisor |
| Load testing | k6 |
| Deployment | Docker Compose on a 4 GB VPS; GitHub Actions for build and tests |
| AI explanations | Azure OpenAI (GPT), with a template fallback |

---

## 9. 60-second pitch

> "Fuel stations in this network hold less than one day of fuel. If nobody acts, customer service falls from 88% to 15% in six days, and supply stops on day two.
>
> We tested the official simulator and found three hidden traps that silently destroy fuel. Full depots throw away supply: 73,000 litres. Full stations throw away deliveries. And a truck sent on a road that closes at the wrong moment loses all of its fuel.
>
> Jalani Control Tower forecasts demand, predicts which station will run out and how sure it is, and plans shipments with an optimizer that respects every rule and avoids every trap.
>
> Routine refills run automatically. Big decisions, like moving fuel between regions or rationing, go to a human with the reasons, the expected effect and a deadline.
>
> When the simulator or our own services fail, Jalani drops to a safer mode, keeps the operator informed, and recovers by itself.
>
> And because the simulator is deterministic, we prove our results with a fair benchmark on a second copy of the official simulator."

---

## 10. Likely judge questions

**Q1. Why not machine learning for everything?**
We use ML where it helps: the demand forecast and anomaly detection. For the allocation itself, the problem has hard limits and clear goals, so an optimizer is more accurate and easier to explain.

**Q2. How do you know your forecast is good?**
We measure its error all the time (WAPE) and compare it with a simple "same time yesterday" forecast. Both are shown on the dashboard.

**Q3. What if the optimizer gives a bad answer?**
Every shipment is checked against all simulator rules before it is sent. If the optimizer fails or times out, the backup rule takes over automatically.

**Q4. How do you avoid sending the same shipment twice when the API returns errors?**
Each shipment has a fixed ID (idempotency key). Sending the same ID again returns the original shipment instead of creating a new one.

**Q5. Why does a human need to approve anything, if time is so fast?**
Only important decisions need approval: moving fuel between regions, rationing, and low-confidence cases. Each one has a deadline. Routine work is automatic, so the network stays safe even at full speed.

**Q6. What happens when supply stops on day 2.2?**
There isn't enough fuel for everyone, so the optimizer spreads shortages fairly. The operator can set priorities, for example for highway or industrial diesel.

**Q7. How do you handle a road that will close soon?**
Scheduled closures are visible in advance. We fill the station before the road closes; trucks already on the road still arrive. We never send a truck just as the road closes, and we cancel waiting shipments so their fuel is returned.

**Q8. What if the simulator goes down during the demo?**
Jalani enters SAFE HOLD. It shows the last known state and an estimate, stops sending, and resyncs automatically when the simulator returns.

**Q9. Is anything hard-coded?**
No. The network, capacities, tick length and supply schedule are all read from the simulator. The only outside information is the documented daily demand pattern, which we use as a starting point for the forecast and then learn from real data.

**Q10. How would this work in the real world?**
The same design applies to a real fuel operations center: forecast, optimize, keep a human in control, and survive failures. We clearly mark everything as **simulation only**. No real fuel infrastructure is touched.
