# Visual centering and precise flight commands

Proposed flight-control architecture: reach a waypoint, hold position, then use fresh visual feedback and measured, variable movements to align a selected target with the camera crosshair.

Status: design for repository consideration, not an implemented or flight-validated capability. No repository push or deployment is included.

## What we want to prove

A local conversational model should understand relaxed instructions such as “forward point five” and translate them into explicit flight-tool calls. A visual decision model should also be able to propose movements from camera feedback. Both routes must use one movement controller rather than competing command streams.

The immediate objective is to demonstrate reliable command execution and repeatable visual alignment with the existing flight controller. RTK is a possible later addition, not a prerequisite for this prototype. “Pinpoint accuracy” is the goal to measure, not an accuracy already established by the existing tests.

## Roles and information flow

- OpenIPC supplies the camera feed. A shared decoded feed can serve the perception and conversational components.
- OpenCV and potentially YOLO provide target detection and tracking. Approximately 30 frames per second is the proposed perception target, subject to hardware benchmarking.
- A visual conversational or decision model receives selected images and detections, potentially around five frames per second. It chooses a target and proposes the next direction, distance or rotation.
- One controller validates and sequences those proposals through the existing flight tools and MAVLink connection. A detector does not independently issue flight commands.
- The Pixhawk handles flight stabilization and position holding between commands. The visual model is not the stabilization loop, and pilot takeover retains priority.

## Flight sequence

1. **Reach the waypoint.** Travel to the approximate location of interest. The waypoint provides a search area, not necessarily the final precise target position.
2. **Hold position and inspect.** Use GUIDED position hold while processing the camera view. There is no requirement to switch to LOITER between every small movement. An AUTO mission, if used to reach the location, requires an explicit handover to visual positioning rather than simultaneous competing control.
3. **Select and track the target.** Use detections and imagery to identify the object to align with the center crosshair. Maintain target identity between observations.
4. **Choose a measured movement.** The visual model can request forward, backward, left, right or rotation, with a distance chosen for that observation. Examples are 0.25 metres and 0.5 metres, not a mandatory fixed step size.
5. **Execute one movement and hold.** Route the request through the existing command path. Wait for movement completion, then hold while obtaining fresh feedback. Do not accumulate a queue of movements based on old images.
6. **Reassess and refine.** Observe the remaining displacement from the crosshair, make another measured correction if needed, and repeat. Smaller corrections near the target are part of the proposed convergence strategy.
7. **Confirm visual alignment.** Declare the positioning objective complete only when the selected target remains within a defined crosshair tolerance over fresh observations. The tolerance and persistence requirement need to be specified and tested.
8. **Continue deliberately.** After explicit completion, the controller can send the next waypoint objective. No activity, missing detections or a lost camera feed must not be mistaken for successful completion.

## Turning dialogue into flight commands

Equivalent everyday phrases should resolve to the same structured command. For example, “forward point five” specifies a forward movement of 0.5 metres. The model must resolve the intended reference frame rather than invent a destination.

Camera-relative and aircraft-relative directions are not automatically north and east. The movement adapter needs the camera orientation and aircraft heading appropriate to the observation before translating a requested nudge into the existing flight tool's coordinate frame. Rotation changes that relationship and must be accounted for on the next movement.

Remain in GUIDED for this positioning loop and hold between moves. Do not automatically reissue GUIDED if a pilot has taken control. The precise tool calls and completion behavior must be checked against the repository implementation during integration.

## How visual centering produces precision

The loop uses observed alignment error to refine the aircraft position rather than first requiring a GPS ground coordinate for every detected pixel. This reduces dependence on full pixel-to-ground projection for the centering task, but it does not remove the need for correct camera alignment, movement scaling or an understood reference frame.

A downward-looking camera provides the crosshair reference. Rangefinders and available surface or elevation information are intended to supply height-above-ground context. These are proposed inputs, not verified integrations in this note. Height and camera orientation affect how image displacement relates to physical movement.

The Pixhawk corrects drift during the hold while the visual model chooses its next action. Position hold is not perfectly motionless. Centering an object in an image is also not, by itself, proof of centimetre-level ground-position accuracy: performance depends on positioning quality, height, image resolution, camera alignment, feedback delay and disturbances.

Five frames per second supplies an image every 200 milliseconds, but does not prove that inference and command execution finish within 200 milliseconds. Measure capture-to-decision-to-movement latency on the intended machine. Image update rate and movement command rate are separate quantities.

## Prototype and verification

Build the prototype around variable movement proposals, one command owner, GUIDED hold and fresh feedback after each movement. Start with scripted observations and the existing simulator-backed command path before real-board integration.

Verify that:

- Conversational instructions produce the intended tool arguments and units.
- Aircraft or camera-relative directions are translated correctly at different headings.
- Requested displacements produce the expected movement and a hold afterward.
- The loop converges toward the selected crosshair target without switching targets accidentally.
- Stale frames, target loss or uncertain feedback stop further corrections and leave the controller holding where possible.
- Pilot takeover prevents the loop from reclaiming control.
- Completion is explicit and distinct from inactivity before advancing to another waypoint.

Record final image error, independently measured physical alignment error, convergence time, movement count and drift during the hold. Agree on an acceptable physical tolerance before describing the result as pinpoint accuracy.

Existing command-stack tests are a foundation, not proof that this new visual loop works. The next implementation must connect perception, frame-to-motion translation, command completion and visual confirmation, then report which parts were actually exercised in simulation and on hardware.
