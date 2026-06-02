#!/home/sean/code/kubefire/.venv/bin/python

from kubernetes import client, config
import json
import requests
import urllib3
import argparse

# Disable insecure request warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_solidfire_paired_volumes(mvip, username, password):
    url = f"https://{mvip}/json-rpc/12.5"
    payload = {
        "method": "ListActivePairedVolumes",
        "params": {},
        "id": 1
    }
    try:
        response = requests.post(url, json=payload, auth=(username, password), verify=False)
        response.raise_for_status()
        data = response.json()
        
        paired_volumes = {}
        if 'result' in data and 'volumes' in data['result']:
            for volume in data['result']['volumes']:
                # Ensure the volume has pairs before accessing
                if volume.get('volumePairs'):
                    paired_volumes[volume['name']] = {
                        "remote_volume_id": volume['volumePairs'][0]['remoteVolumeID'],
                        "remote_volume_name": volume['volumePairs'][0]['remoteVolumeName']
                    }
        return paired_volumes
    except Exception as e:
        print(f"Failed to fetch paired volumes from SolidFire: {e}")
        return {}

def export_solidfire_data(kubeconfig_path, prod_mvip, sf_user, sf_pass):
    # Load kube config from default location or specific path
    if kubeconfig_path:
        config.load_kube_config(config_file=kubeconfig_path)
    else:
        config.load_kube_config()

    v1 = client.CoreV1Api()
    storage_v1 = client.StorageV1Api()

    PROVISIONER_NAME = "csi.solidfire.com"

    # 1. Query all SolidFire StorageClasses
    scs = storage_v1.list_storage_class()
    solidfire_scs = {}
    for sc in scs.items:
        if sc.provisioner == PROVISIONER_NAME:
            solidfire_scs[sc.metadata.name] = {
                "name": sc.metadata.name,
                "parameters": sc.parameters,
                "reclaim_policy": sc.reclaim_policy,
                "volume_binding_mode": sc.volume_binding_mode
            }

    # 2. Query all PVs provisioned by SolidFire CSI
    pvs = v1.list_persistent_volume()
    solidfire_pvs = {}
    
    # Get available paired volumes from SolidFire
    sf_paired_volumes = get_solidfire_paired_volumes(prod_mvip, sf_user, sf_pass)
    
    for pv in pvs.items:
        if pv.spec.csi and pv.spec.csi.driver == PROVISIONER_NAME:
            dr_mapping = sf_paired_volumes.get(pv.metadata.name, None)
            
            solidfire_pvs[pv.metadata.name] = {
                "name": pv.metadata.name,
                # volumeHandle contains the SolidFire volume ID
                "volume_handle": pv.spec.csi.volume_handle, 
                "capacity": pv.spec.capacity,
                "access_modes": pv.spec.access_modes,
                "storage_class": pv.spec.storage_class_name,
                "claim_ref": {
                    "name": pv.spec.claim_ref.name if pv.spec.claim_ref else None,
                    "namespace": pv.spec.claim_ref.namespace if pv.spec.claim_ref else None
                },
                "dr_replication": dr_mapping
            }

    # 3. Query all PVCs globally and filter ones bound to SolidFire PVs
    pvcs = v1.list_persistent_volume_claim_for_all_namespaces()
    solidfire_namespaces = set()
    solidfire_pvcs = []
    
    for pvc in pvcs.items:
        if pvc.spec.volume_name in solidfire_pvs:
            solidfire_namespaces.add(pvc.metadata.namespace)
            solidfire_pvcs.append({
                "name": pvc.metadata.name,
                "namespace": pvc.metadata.namespace,
                "storage_class": pvc.spec.storage_class_name,
                "volume_name": pvc.spec.volume_name,
                "requests": pvc.spec.resources.requests
            })

    # Prepare structured payload describing the state
    dr_recovery_blueprint = {
        "storage_classes": solidfire_scs,
        "namespaces": list(solidfire_namespaces),
        "persistent_volumes": solidfire_pvs,
        "persistent_volume_claims": solidfire_pvcs
    }

    # Outputs as JSON mapping, ready to be sent to an S3 bucket or saved locally
    print(json.dumps(dr_recovery_blueprint, indent=2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export SolidFire CSI state and mapping for DR failover.")
    parser.add_argument("--kubeconfig", help="Path to the kubeconfig file", default=None)
    parser.add_argument("--mvip", help="SolidFire Management VIP (MVIP) address", default="192.168.1.34")
    parser.add_argument("--username", help="SolidFire admin username", default="admin")
    parser.add_argument("--password", help="SolidFire admin password", default="----")
    
    args = parser.parse_args()

    export_solidfire_data(
        kubeconfig_path=args.kubeconfig,
        prod_mvip=args.mvip,
        sf_user=args.username,
        sf_pass=args.password
    )
