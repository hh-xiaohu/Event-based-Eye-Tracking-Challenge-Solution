import argparse, json, os, csv
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader
from utils.metrics import process_array
import importlib
from dataset.ThreeET_plus import ThreeETplus_Eyetracking
from dataset.custom_transforms import ScaleLabel, NormalizeLabel, \
    TemporalSubsample, NormalizeLabel, SliceLongEventsToShort, \
    EventSlicesToMap, SliceByTimeEventsTargets, \
    Jitter
import tonic.transforms as transforms
from tonic import SlicedDataset, DiskCachedDataset
import importlib
import numpy as np
import pandas as pd
import time
import pdb
def test(args, model):
    num_workers=24
    # test data loader always cuts the event stream with the labeling frequency
    factor = args.spatial_factor
    temp_subsample_factor = args.temporal_subsample_factor
    if temp_subsample_factor == 0.2:
        outputs_oof = np.zeros((8740, 3))
        outputs_inds = []
        for i in range(5):
            label_transform = transforms.Compose([
                ScaleLabel(factor),
                TemporalSubsample(temp_subsample_factor),
                NormalizeLabel(pseudo_width=640*factor, pseudo_height=480*factor)
            ])

            test_data_orig = ThreeETplus_Eyetracking(save_to=args.data_dir, split="test", \
                            transform=transforms.Downsample(spatial_factor=factor),
                            target_transform=label_transform, ind=i)

            slicing_time_window = args.test_length*int(10000/temp_subsample_factor) #microseconds
            test_stride_time = int(10000/temp_subsample_factor*args.test_stride) #microseconds
            test_slicer=SliceByTimeEventsTargets(slicing_time_window, overlap=slicing_time_window-test_stride_time, \
                            seq_length=args.test_length, seq_stride=args.test_stride, include_incomplete=True)

            post_slicer_transform = transforms.Compose([
                SliceLongEventsToShort(time_window=int(10000/temp_subsample_factor), overlap=0, include_incomplete=True),
                EventSlicesToMap(sensor_size=(int(640*factor), int(480*factor), 2), \
                                        n_time_bins=args.n_time_bins, per_channel_normalize=args.voxel_grid_ch_normaization,
                                        map_type=args.map_type),
            ])

            test_data = SlicedDataset(test_data_orig, test_slicer, transform=post_slicer_transform, \
                metadata_path=f"{args.metadata_dir}/3et_test_l{args.test_length}s{args.test_stride}_ch{args.n_time_bins}_t{args.map_type}_dt{temp_subsample_factor}_ind{i}")
            # cache the dataset to disk to speed up training. The first epoch will be slow, but the following epochs will be fast.
            # pdb.set_trace()
            test_data = DiskCachedDataset(test_data, cache_path=f"{args.cache_dir}/test_l{args.test_length}s{args.test_stride}_ch{args.n_time_bins}_t{args.map_type}_dt{temp_subsample_factor}_ind{i}")

            args.batch_size = 1
            test_loader = DataLoader(test_data, batch_size=args.batch_size, shuffle=False, \
                                    num_workers=num_workers)
            
            if not os.path.exists(args.log_dir):
                os.makedirs(args.log_dir)
            outputs_list = []
            targets_list = []
            cuda_times = []
            for batch_idx, (data, target) in enumerate(test_loader):
                data = data.to("cuda:0")
                with torch.no_grad():
                    # starter.record()
                    start = time.time()
                    try:
                        output, aux = model(data)
                    except:
                        output = model(data)
                    end = time.time()
                    cuda_times.append((end - start) * 1000)
                output = output * torch.tensor((640*factor, 480*factor)).to("cuda:0")
                output = output * 0.125 / factor
                outputs_list.append(output.detach().cpu())
                targets_list.append(target.detach().cpu())
            outputs_list = torch.cat(outputs_list, dim=0)
            targets_list = torch.cat(targets_list, dim=0)
            # pdb.set_trace()
            outputs_list = torch.cat([outputs_list, targets_list[:, :, -1].unsqueeze(-1)], dim=2)
            # 
            outputs, pred_lengths, actual_lengths = process_array(outputs_list, targets_list[:, :, :3], test=True)
            outputs = np.concatenate(outputs, axis=0)
            pred_lengths = np.cumsum(pred_lengths)
            actual_lengths = np.cumsum(actual_lengths)

            for k in range(len(outputs)):
                # pdb.set_trace()
                inds = np.where(pred_lengths > k)[0][0]
                if inds == 0:
                    inds_ = inds
                    outputs_ind = k * 5 + i + inds_
                else:
                    inds_ = actual_lengths[inds-1]
                    outputs_ind = (k - pred_lengths[inds-1]) * 5 + i + inds_
                # pdb.set_trace()
                outputs_ind = int(outputs_ind)
                try:
                    outputs_oof[outputs_ind] = outputs[k]
                except:
                    import pdb;pdb.set_trace()
                outputs_inds.append(outputs_ind)
        outputs = outputs_oof
    else:
        label_transform = transforms.Compose([
            ScaleLabel(factor),
            TemporalSubsample(temp_subsample_factor),
            NormalizeLabel(pseudo_width=640*factor, pseudo_height=480*factor)
        ])

        test_data_orig = ThreeETplus_Eyetracking(save_to=args.data_dir, split="test", \
                        transform=transforms.Downsample(spatial_factor=factor),
                        target_transform=label_transform)

        slicing_time_window = args.test_length*int(10000/temp_subsample_factor) #microseconds
        test_stride_time = int(10000/temp_subsample_factor*args.test_stride) #microseconds
        test_slicer=SliceByTimeEventsTargets(slicing_time_window, overlap=slicing_time_window-test_stride_time, \
                        seq_length=args.test_length, seq_stride=args.test_stride, include_incomplete=True)

        post_slicer_transform = transforms.Compose([
            SliceLongEventsToShort(time_window=int(10000/temp_subsample_factor), overlap=0, include_incomplete=True),
            EventSlicesToMap(sensor_size=(int(640*factor), int(480*factor), 2), \
                                    n_time_bins=args.n_time_bins, per_channel_normalize=args.voxel_grid_ch_normaization,
                                    map_type=args.map_type),
        ])

        test_data = SlicedDataset(test_data_orig, test_slicer, transform=post_slicer_transform, \
            metadata_path=f"{args.metadata_dir}/3et_test_l{args.test_length}s{args.test_stride}_ch{args.n_time_bins}_t{args.map_type}_dt{temp_subsample_factor}_ind0")

        test_data = DiskCachedDataset(test_data, \
                                    cache_path=f"{args.cache_dir}/test_l{args.test_length}s{args.test_stride}_ch{args.n_time_bins}_t{args.map_type}_dt{temp_subsample_factor}_ind0")

        args.batch_size = 32
        test_loader = DataLoader(test_data, batch_size=args.batch_size, shuffle=False, \
                                num_workers=num_workers)
        
        # evaluate on the validation set and save the predictions into a csv file.
        if not os.path.exists(args.log_dir):
            os.makedirs(args.log_dir)
        # warm up the GPU, so that the first inference time is not too slow
        for _ in range(50):
            with torch.no_grad():
                _ = model(torch.tensor(test_data[0][0]).unsqueeze(0).to("cuda:0"))
        outputs_list = []
        targets_list = []
        cuda_times = []
        # starter, ender = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
        for batch_idx, (data, target) in enumerate(test_loader):
            data = data.to("cuda:0")
            with torch.no_grad():
                # starter.record()
                start = time.time()
                try:
                    output, aux = model(data)
                except:
                    output = model(data)
                end = time.time()
                # ender.record()
                # torch.cuda.synchronize()
                # curr_time = starter.elapsed_time(ender)
                cuda_times.append((end - start) * 1000)
            output = output * torch.tensor((640*factor, 480*factor)).to("cuda:0")
            output = output * 0.125 / factor
            outputs_list.append(output.detach().cpu())
            targets_list.append(target.detach().cpu())
        outputs_list = torch.cat(outputs_list, dim=0)
        targets_list = torch.cat(targets_list, dim=0)
        # pdb.set_trace()
        outputs_list = torch.cat([outputs_list, targets_list[:, :, -1].unsqueeze(-1)], dim=2)
        # 
        outputs, pred_lengths, actual_lengths = process_array(outputs_list, targets_list[:, :, :3], test=True)
        
        if temp_subsample_factor == 0.2:
            for i in range(len(outputs)):
                output = np.zeros((int(actual_lengths[i]), 3))
                for j in range(len(outputs[i])):
                    if j * 5 <= len(output):
                        output[j*5:(j+1)*5] = outputs[i][j]
                    else:
                        output[j*5:] = outputs[i][j]
                outputs[i] = output
        else:
            pass
        outputs = np.concatenate(outputs, axis=0)
    
    df = pd.DataFrame(outputs, columns=['x', 'y', 'z'])
    df[['x', 'y']].to_csv(os.path.join('./ckpt', 'submission_test.csv'), index_label='row_id')
    print(f"Average inference time: {sum(cuda_times)/len(cuda_times)/args.batch_size} ms")
    with open(os.path.join('./ckpt', 'inference_time.txt'), 'w') as f:
        f.write(f"Average inference time: {sum(cuda_times)/len(cuda_times)/args.batch_size} ms")
    print("Submission file has been saved to ", os.path.join('./ckpt', 'submission_test.csv'))

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    
    # a config file 
    parser.add_argument("--config_file", type=str, default='sliced_baseline.json', \
                        help="path to JSON configuration file")
    # load weights from a checkpoint
    parser.add_argument("--checkpoint", type=str)
    parser.add_argument("--test_length", type=int)
    parser.add_argument("--test_stride", type=int)
    parser.add_argument("--temporal_subsample_factor", type=float)
    parser.add_argument("--spatial_factor", type=float)
    parser.add_argument("--map_type", type=str)
    args = parser.parse_args()

        # Load hyperparameters from JSON configuration file
    if args.config_file:
        with open(os.path.join('./configs', args.config_file), 'r') as f:
            config = json.load(f)
        # Overwrite hyperparameters with command-line arguments
        for key, value in vars(args).items():
            if value is not None:
                config[key] = value
        args = argparse.Namespace(**config)
    else:
        raise ValueError("Please provide a JSON configuration file.")

    # also dump the args to a JSON file in MLflow artifact
    print(json.dumps(config, sort_keys=False))

    # Define your model, optimizer, and criterion
    model = importlib.import_module(f"model.{args.model}").Model(args).to("cuda:0")
    model = nn.DataParallel(model)
    # load weights from a checkpoint
    # pdb.set_trace()
    if args.checkpoint:
        model.load_state_dict(torch.load(args.checkpoint))
    else:
        raise ValueError("Please provide a checkpoint file.")
    model.eval()
    test(args, model)