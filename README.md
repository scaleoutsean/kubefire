**KubeFire** - failover and failback for Kubernetes with SolidFire using SolidFire CSI

- [Introduction](#introduction)
- [SolidFire CSI scenarios](#solidfire-csi-scenarios)
  - [Externally managed SolidFire failover with Terraform or scripts](#externally-managed-solidfire-failover-with-terraform-or-scripts)
  - [Kubernetes-integrated SolidFire failover](#kubernetes-integrated-solidfire-failover)
  - [Notes, tips and best practices](#notes-tips-and-best-practices)
    - [Volume attributes](#volume-attributes)
    - [Volume resizing for paired volumes](#volume-resizing-for-paired-volumes)
    - [Static PV pattern: cattle vs pet volumes](#static-pv-pattern-cattle-vs-pet-volumes)
    - [Dynamic PV pattern: Kubernetes-managed replication](#dynamic-pv-pattern-kubernetes-managed-replication)
    - [Rehearsals and testing](#rehearsals-and-testing)
    - [Other noteworthy differences vs Trident CSI](#other-noteworthy-differences-vs-trident-csi)
    - [Kubernetes Volume Snapshot and Volume Group Snapshot replication](#kubernetes-volume-snapshot-and-volume-group-snapshot-replication)
    - [Site failover with dedicated vs. shared SolidFire clusters](#site-failover-with-dedicated-vs-shared-solidfire-clusters)
- [Tools](#tools)


## Introduction

Older versions of this README have information about Trident CSI, but after SolidFire CSI came out I realized there's no reason to cover Trident CSI - it doesn't support, and I don't think it will, failback with `solidfire-san`, so why bother reading how it "could" be done?

This guide is now only for SolidFire CSI.

## SolidFire CSI scenarios

[SolidFire CSI](https://scaleoutsean.github.io/2026/03/06/solidfire-csi-driver.html) is a community CSI driver for SolidFire that, among other things, makes it easier to handle storage cluster failover and failback. That was one of the main reasons I created it. [Get it here](https://github.com/scaleoutsean/solidfire-csi).

You won't need any special "tool" or script to failover or failback Kubernetes with SolidFire CSI. Simply check the documentation and deploy.

Long story short, each SolidFire CSI driver's `volumeHandle` refers to a SolidFire Volume ID. The rest is simply about creating (replication) volume pairs and deciding on the direction of replication. Flip that `volumeHandle` to the ID of the paired volume. That's it.

```yaml
spec:
  accessModes:
  - ReadWriteOnce
  capacity:
    storage: 1Gi
  claimRef:
    apiVersion: v1
    kind: PersistentVolumeClaim
    name: test-pvc
    namespace: default
    resourceVersion: "110007"
    uid: d173a454-5071-4166-be84-3fec60e95938
  csi:
    controllerExpandSecretRef:
      name: solidfire-secret
      namespace: default
    controllerPublishSecretRef:
      name: solidfire-secret
      namespace: default
    driver: csi.solidfire.com
    fsType: ext4
    nodeStageSecretRef:
      name: solidfire-secret
      namespace: default
    volumeAttributes:
      storage.kubernetes.io/csiProvisionerIdentity: 1769874269794-1851-csi.solidfire.com
    volumeHandle: "122"     # <======= HERE 
  persistentVolumeReclaimPolicy: Delete
  storageClassName: solidfire-bronze
```

### Externally managed SolidFire failover with Terraform or scripts

You can also do that with Trident CSI using the same tool that can be used with SolidFire CSI, [Terraform Provider for SolidFire](https://github.com/scaleoutsean/terraform-provider-solidfire), but you'll have to deal with Trident CSI (fail-back) separately. 

SolidFire CSI gets out of your way: there are no "backends" or similar constructs that you need to manage or recover. There's no "volume import" feature either, because SolidFire CSI is stateless. So once you're done flipping the direction of replication, just create static PVCs which, considering that you have all the volume IDs and hence their names and Kubernetes information, too, is straightforward.

**NOTE:** as usual, replication target volumes are always in `replicationTarget` mode, which means Kubernetes on target site should first have deployments scaled down to 0, or not created until failover to target site happens and the volumes have been flipped to `readWrite` mode.

### Kubernetes-integrated SolidFire failover

My preferred approaches (not in order of preference) are Argo CD (or similar) on each site, and a "witness site" approach with management plane where failover decisions are made by tenants using a CLI or Web UI located on "witness site". I haven't started working on this as I don't know of anyone who needs this.

### Notes, tips and best practices

#### Volume attributes

- SolidFire CSI controller [exports Prometheus metrics](https://scaleoutsean.github.io/2026/05/17/couple-o-releases.html#solidfire-csi-v100) and also injects metadata into SolidFire volume attributes (Trident CSI does that part, too)
- SolidFire Collector collects volume attributes
- These aren't supposed to be used as the Source of Truth "database" as they can be wiped, changed out-of-band, etc. They're for more convenient monitoring and work only if you don't use them in ways to get them out of sync (which you can do by reusing PVs that sit around thanks to `Retain`)

If you collect these you get complete Kubernetes-to-SolidFire mapping and monitoring that you can additionally augment with Kubernetes cluster metrics, not just for performance but also configuration monitoring and logging.

#### Volume resizing for paired volumes

- This should be managed separately and not figured out "as you go" in the middle of a site failover. Although SolidFire CSI and Ansible, and Terraform make this easy, the risk isn't that it can't be done in course of troubleshooting a fail-over or fail-back, but that enlarged target volumes won't be replicated to destination that hasn't been resized to the same (or larger, although that's silly) capacity. That's why it's recommended to have this in order and managed separately from failover-failback
- You may use own scripts, obviously, or Ansible (to get facts) or [Longhorny](https://github.com/scaleoutsean/longhorny)
- Longhorny provides an easy site-to-site comparison report, but it can also be used to resize volumes although this doesn't mean you should use Longhorny to resize (depending on where you want to manage volumes (see below), resizing in Longhorny may be the wrong way to do it). The main thing that is valid for Kubernetes and SolidFire CSI environments is that *report*. You can also reuse that source code to build own tools
- For the geeks out there, you could use SolidFire Collector to figure out pairings and find size discrepancies among paired volumes using InfluxDB SQL queries, either directly or from Grafana dashboards where you could also create alerts for these situations

#### Static PV pattern: cattle vs pet volumes

While some prefer to manage everything in Kubernetes, many users still prefer to manage volumes as "pets".

- Pet volumes: pre-create source *and destination* volumes with [Ansible Collection for SolidFire](https://github.com/scaleoutsean/netapp.solidfire) or [Terraform Provider for SolidFire](https://github.com/scaleoutsean/terraform-provider-solidfire) or own script. Then import static volumes to SolidFire CSI on each site. Now failover and failback is simply a matter of flipping the direction of replication (volume pairing relationship). Note that you can resize volumes in SolidFire CSI, which then makes the reality out of sync with Terraform's state, so pick one way to do it, make sure it works the way you expect, and stick with it
- Cattle volumes: don't replicate these. These are semi-ephemeral - you don't need a copy and their replication consumes bandwidth
- Remember to set "pet" PVCs to "Retain". SolidFire CSI also provides the option to not Purge volumes on PVC Delete when retention policy is Delete (Trident CSI always purges deleted volumes with `solidfire-san`) if you want to use the retention policy "Delete" but be able to rescue fat-fingered volumes before they expire from SolidFire's Recycle Bin. Deleted but not-yet-purged volumes can be restored and brought back to Kubernetes with SolidFire CSI as static PVCs

#### Dynamic PV pattern: Kubernetes-managed replication

If you want to use Kubernetes-based workflow (GitOps, ArgoCD, etc.) as your control plane, that's possible and easy with Trident CSI.

SolidFire CSI has no "backend", provides sufficient tools to make this work and doesn't stand in your way. A volume is defined by MVIP (which uniquely defines a SolidFire cluster) and `volumeHandle`. All you need to do to replicate any volume between clusters is get a list of volumes and set them up for replication.

To flip back:
- get a list of SolidFire CSI volumes at the remote site and compare to see if anything changed while operating on the remote site (new volumes, removed volumes)
- if any new PVCs/PVs exist, create new replication relationships so that all volumes are accounted for and replicating for failback
- if any pre-failover PVCs/PVs have been removed, find out why and whether it's OK to remove them at the site you plan to fail back to (or do that later)
- the last step is to check for any size differences in replicated volume pairs, which may appear due to resizing while operating at the remote site. You should have a separate procedure that updates both sides of a replication relationship and not have to "discover" this now. If any volume has been expanded while active on fail-over site, expand its source peer on the site that you plan to fail back to and let it sync
- with the original source site volumes in `replicationTarget` mode, once all replication pairs are in sync, scale the active (remote) site to 0, flip replication relationships to promote the original site to active and seconds later you may fail back your workloads by scaling up deployments on your primary site

#### Rehearsals and testing

With SolidFire CSI you won't need to deal with stuck backends or other weirdness. If your volumes are replicating to a remote site it's trivial to perform site or storage cluster failover rehearsals:

- on replication target site, simply create a test namespace, clone volumes, create static PVs from clones, test your application and delete them after you're done testing
- CSI volumes can be cloned directly from replication targets (in `replicationTarget` mode) but also from SolidFire snapshots
  - if you create snapshots and enable them for replication at the source, you'll be able to use them (perhaps you create these so that they're [application-aware](https://scaleoutsean.github.io/2024/03/23/velero-netapp-verda-scripts-and-trident.html)). If not, you can create snapshots on demand or simply clone current state of a replication target (obviously, this will be a "cash-consistent" clone).
  - you can create clones from volumes at the target site. While this is *ad hoc*, it will work just the same
- use a "purge"-enabled Storage Class on SolidFire CSI if testing at scale, to not max out your tenant's quota or hit some other limit (metadata capacity, block capacity, etc.)
- SolidFire CSI on the remote site simply needs to create static PVs for these clones

#### Other noteworthy differences vs Trident CSI

- It is recommended to create a mapping for QoS policies if you use those. You can have those hard-coded in SolidFire Storage Classes, so that you don't have to do anything special: if "Silver" is QoS Policy ID `1` at your production site and `2` on your DR site cluster, simply prepare storage classes that are named the same, but use different QoS Policy IDs. If you don't do that, you can use "default" SolidFire QoS values for volumes and not manage QoS. Or you can ignore QoS management and retype volumes after failover if you discover you need to use that site longer than expected - SolidFire CSI can do that too

#### Kubernetes Volume Snapshot and Volume Group Snapshot replication

- You can create a VolumeSnapshotClass that enables snapshot replication for a snapshot (it will work if the volume is already paired, which you can do from Longhorny or on your own)

#### Site failover with dedicated vs. shared SolidFire clusters

If your SolidFire cluster is shared by several Kubernetes clusters, you should consider if your failover scenarios should include just some of the tenants (two or more Kubernetes clusters) or all tenants (all Kubernetes clusters, or single Kubernetes cluster).
- Different Kubernetes clusters should use SolidFire each with own SolidFire account (tenant) identity.
- For granular failover (of individual SolidFire tenants), you need to flip just volume pairs owned by those tenants. Longhorny is suggested for monitoring because while it can flip the direction of replication, it does it for *all paired volumes* (and hence all tenants). It would need to enhanced to filter by tenant account ID to be able to used for simple tenant(s)-based site failover. Of course, that can be implemented, but it hasn't been done yet

## Tools 

- `./export_solidfire_state.py` exports SolidFire CSI state from a cluster identified by kubeconfig file and SolidFire credentials

```sh
./.venv/bin/python export_solidfire_state.py \
    --kubeconfig ~/.kube/config \
    --mvip 192.168.1.34 \
    --username admin \
    --password ----
```
