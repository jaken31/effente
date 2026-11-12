# Architecture

Two diagrams: how the system fits together, and the data it keeps. Details are in the [TRD](TRD.md).

## High-level design

```mermaid
flowchart LR
    subgraph Machine["Machine under test"]
        M[Motor or fan]
    end

    subgraph Handheld["Handheld device (Arduino UNO Q)"]
        H[Magnetic sensor head<br/>MPU6050 accelerometer]
        subgraph MCU["Microcontroller"]
            S[Sampling<br/>1,000 readings per second]
            UI[Screen and SCAN button]
        end
        subgraph Linux["Linux side"]
            P[Signal pipeline<br/>readings to spectrum]
            N[Small neural network]
            A[Unknown alarm]
            X[Explanation table<br/>verified features only]
            L[(Device log)]
            AG[Agent harness]
        end
    end

    subgraph Laptop["Laptop platform"]
        R[Log receiver]
        D[Dashboard<br/>spectrum, features, history]
        T[Switch-off toggle]
    end

    subgraph Cloud["Online services"]
        C[Claude API]
        TV[Tavily search]
    end

    subgraph Offline["Built ahead of time, on a laptop"]
        REC[(Recordings)]
        TR[Training]
        INT[Mech interp<br/>switch-off tests]
        MF[(Model file<br/>weights + feature table)]
    end

    M -- vibration --> H --> S --> P --> N
    N --> A
    N --> X
    A --> UI
    X --> UI
    N --> L
    L -- WiFi --> R --> D
    T -- feature mask over WiFi --> N
    L --> AG
    AG <--> C
    AG <--> TV
    AG -- maintenance report --> D

    S -. raw captures .-> REC
    REC --> TR --> INT --> MF
    MF -- loaded onto device --> N
    MF --> X
```

Reading it left to right:

1. **Sense.** The magnetic head sits on the machine. The microcontroller samples it on a steady clock.
2. **Decide.** The signal pipeline turns readings into a spectrum, the network diagnoses, the unknown alarm checks whether the pattern is familiar, and the explanation table supplies the reason. The result goes to the screen. None of this needs a network.
3. **Share.** Each reading is logged and uploaded over WiFi to the laptop, where the dashboard shows it.
4. **Act.** The agent reads the log, calls Claude and Tavily, and produces a maintenance report. This is the only part that needs the internet.
5. **Prove.** The switch-off toggle sends a feature mask back to the device, and the diagnosis changes live.

The bottom box happens before any of that: recordings train the network, mech interp finds and tests its features, and the result is one model file loaded onto the device. The same signal pipeline code runs in training and on the device.

The dotted line is the bootstrap: the device captures the raw recordings used for training.

Tracks map onto the boxes: track 1 is the handheld, track 2 is recordings and the signal pipeline, track 3 is training, mech interp and the explanation table, track 5 is the laptop platform and the agent.

## Data model

```mermaid
erDiagram
    DEVICE ||--o{ READING : takes
    MACHINE ||--o{ READING : "is probed in"
    MODEL ||--|{ FEATURE : defines
    READING ||--o{ FEATURE_ACTIVITY : has
    FEATURE ||--o{ FEATURE_ACTIVITY : "measured as"
    MACHINE ||--o{ AGENT_RUN : "is reported on by"
    AGENT_RUN ||--o| REPORT : produces
    REPORT ||--o{ REPORT_CITATION : cites
    FEATURE ||--o{ REPORT_CITATION : "is cited in"
    RECORDING }o--o{ MODEL : trains

    DEVICE {
        string device_id PK
    }
    MACHINE {
        string machine_id PK
    }
    RECORDING {
        string session_id
        string mount_id
        string label
        int sample_rate_hz
        int fan_rpm
        datetime recorded_at
        int16_array samples
    }
    MODEL {
        json layer_sizes
        json weights
        json normalisation
        json class_names
        float unknown_threshold
    }
    FEATURE {
        string name PK
        json unit_indices
        string sentence
    }
    READING {
        string device_id FK
        string machine_id FK
        datetime at
        string diagnosis
        float confidence
        float unknown_score
        json mask
        json spectrum
    }
    FEATURE_ACTIVITY {
        string feature_name FK
        float value
    }
    AGENT_RUN {
        string machine_id FK
        datetime at
        string model
        json usage
        bool ok
        string reason
        json problems
    }
    REPORT {
        string summary
        string priority
        string recommended_action
        string part_reference
    }
    REPORT_CITATION {
        string feature_name FK
    }
```

Reading it:

- **Recording** is training data: a labelled capture from the rig. **Reading** is what the device produces in use: one diagnosis per window.
- **Feature** is the centre of the design. Features come from the model, show up as activity in each reading, and are the only things a report may cite.
- **Agent run** is one attempt to write a report. It has a report only if the checks passed.

In storage these are files, not database tables. Recordings are `.npz` files. The model and its features are one JSON file. Readings and agent runs are one JSON object per line. Feature activity and citations live inside their parent records.

## Known gaps in the formats

1. **Readings do not record which model produced them.** After a retrain, old and new diagnoses cannot be told apart, and a feature name may mean something different. Add a model version to the model file and to each reading. Fix before recording real data.
2. **Recordings have no machine ID.** Fine with one demo fan, a problem with two.
3. **Machines and devices are only IDs.** There is nowhere to store a machine's name, location or rotation speed.
