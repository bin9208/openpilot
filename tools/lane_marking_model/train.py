#!/usr/bin/env python3
import json
import pathlib
import os
import argparse
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader
from torchvision import datasets, transforms, models
import numpy as np
import matplotlib.pyplot as plt

def resolve_config_path(config_arg):
    config_path = pathlib.Path(config_arg)
    if config_path.is_absolute() or config_path.exists():
        return config_path
    return pathlib.Path(__file__).parent / config_path

def get_model(model_name, num_classes):
    if model_name.lower() == "resnet18":
        try:
            weights = models.ResNet18_Weights.DEFAULT
            model = models.resnet18(weights=weights)
        except AttributeError:
            model = models.resnet18(pretrained=True)
        model.fc = nn.Linear(model.fc.in_features, num_classes)
    elif model_name.lower() == "mobilenet_v3_small":
        try:
            weights = models.MobileNet_V3_Small_Weights.DEFAULT
            model = models.mobilenet_v3_small(weights=weights)
        except AttributeError:
            model = models.mobilenet_v3_small(pretrained=True)
        model.classifier[3] = nn.Linear(model.classifier[3].in_features, num_classes)
    else:
        raise ValueError(f"Unsupported model architecture: {model_name}")
    return model

class InMemoryDataset(torch.utils.data.Dataset):
    def __init__(self, image_folder_dataset, transform=None):
        self.samples = []
        self.targets = []
        self.class_to_idx = image_folder_dataset.class_to_idx
        self.classes = image_folder_dataset.classes
        self.transform = transform

        print(f"Loading dataset from {image_folder_dataset.root} into RAM...")
        import torchvision.io as io

        for path, target in image_folder_dataset.samples:
            try:
                img = io.read_image(path, mode=io.ImageReadMode.RGB)
                self.samples.append(img)
                self.targets.append(target)
            except Exception as e:
                print(f"Error loading {path}: {e}")

        print(f"Successfully loaded {len(self.samples)} images into memory.")

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        img = self.samples[idx]
        target = self.targets[idx]

        # Convert to float32 and scale to [0, 1]
        img = img.float() / 255.0

        if self.transform:
            img = self.transform(img)

        return img, target

def main():
    parser = argparse.ArgumentParser(description="Train lane marking classification model")
    parser.add_argument("--config", type=str, default="config.json", help="Path to config file")
    args = parser.parse_args()

    config_path = resolve_config_path(args.config)
    print(f"Loading configuration from {config_path}...")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    crops_dir = pathlib.Path(config["crops_dir"])
    output_dir = pathlib.Path(config["output_dir"])
    outputs_dir = output_dir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    model_arch = config["model_arch"]
    batch_size = config["batch_size"]
    epochs = config["epochs"]
    learning_rate = config["learning_rate"]
    classes = config["classes"]
    num_classes = len(classes)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    # Set up transforms (operating on tensors)
    train_transform = transforms.Compose([
        transforms.RandomRotation(10),
        transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    ])

    val_transform = transforms.Compose([
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    ])

    train_dir = crops_dir / "train"
    val_dir = crops_dir / "val"

    # Load datasets
    train_folder = datasets.ImageFolder(str(train_dir))
    val_folder = datasets.ImageFolder(str(val_dir))

    train_dataset = InMemoryDataset(train_folder, transform=train_transform)
    val_dataset = InMemoryDataset(val_folder, transform=val_transform)

    print(f"Dataset class-to-index mapping: {train_dataset.class_to_idx}")
    class_mapping = {idx: name for name, idx in train_dataset.class_to_idx.items()}

    # Compute class weights for loss balancing
    targets = np.array(train_dataset.targets)
    class_counts = np.bincount(targets)
    total_samples = len(targets)
    class_weights = []
    for count in class_counts:
        weight = total_samples / (num_classes * count) if count > 0 else 1.0
        class_weights.append(weight)

    while len(class_weights) < num_classes:
        class_weights.append(1.0)

    class_weights_tensor = torch.tensor(class_weights, dtype=torch.float32).to(device)
    print(f"Class samples count: {class_counts}")
    print(f"Computed loss weights: {class_weights}")

    num_workers = config.get("num_workers", 0)
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=num_workers, pin_memory=True if num_workers > 0 else False)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False, num_workers=num_workers, pin_memory=True if num_workers > 0 else False)

    # Initialize model
    print(f"Initializing {model_arch}...")
    model = get_model(model_arch, num_classes)
    model = model.to(device)

    criterion = nn.CrossEntropyLoss(weight=class_weights_tensor)
    optimizer = optim.Adam(model.parameters(), lr=learning_rate)

    best_val_acc = 0.0
    history = {"train_loss": [], "train_acc": [], "val_loss": [], "val_acc": []}

    print("Starting training...")
    for epoch in range(1, epochs + 1):
        # Training Phase
        model.train()
        running_loss = 0.0
        correct_train = 0
        total_train = 0

        for inputs, labels in train_loader:
            inputs, labels = inputs.to(device), labels.to(device)

            optimizer.zero_grad()
            outputs = model(inputs)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()

            running_loss += loss.item() * inputs.size(0)
            _, predicted = torch.max(outputs, 1)
            total_train += labels.size(0)
            correct_train += (predicted == labels).sum().item()

        epoch_train_loss = running_loss / len(train_loader.dataset)
        epoch_train_acc = correct_train / total_train

        # Validation Phase
        model.eval()
        running_val_loss = 0.0
        correct_val = 0
        total_val = 0

        with torch.no_grad():
            for inputs, labels in val_loader:
                inputs, labels = inputs.to(device), labels.to(device)
                outputs = model(inputs)
                loss = criterion(outputs, labels)

                running_val_loss += loss.item() * inputs.size(0)
                _, predicted = torch.max(outputs, 1)
                total_val += labels.size(0)
                correct_val += (predicted == labels).sum().item()

        epoch_val_loss = running_val_loss / len(val_loader.dataset)
        epoch_val_acc = correct_val / total_val

        history["train_loss"].append(epoch_train_loss)
        history["train_acc"].append(epoch_train_acc)
        history["val_loss"].append(epoch_val_loss)
        history["val_acc"].append(epoch_val_acc)

        print(f"Epoch [{epoch}/{epochs}] "
              f"Train Loss: {epoch_train_loss:.4f} Acc: {epoch_train_acc*100:.2f}% | "
              f"Val Loss: {epoch_val_loss:.4f} Acc: {epoch_val_acc*100:.2f}%")

        # Save checkpoint to outputs directory as best_model.pth
        if epoch_val_acc >= best_val_acc:
            best_val_acc = epoch_val_acc
            checkpoint = {
                "epoch": epoch,
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "class_to_idx": train_dataset.class_to_idx,
                "idx_to_class": class_mapping,
                "config": config
            }
            checkpoint_path = outputs_dir / "best_model.pth"
            torch.save(checkpoint, checkpoint_path)
            print(f"  --> Saved new best checkpoint to {checkpoint_path} (Val Acc: {best_val_acc*100:.2f}%)")

    # Save training curves history as training_log.json
    history_path = outputs_dir / "training_log.json"
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)
    print(f"Training history saved to {history_path}")

    # Plot training curves and save as training_curves.png
    plt.figure(figsize=(10, 5))
    plt.subplot(1, 2, 1)
    plt.plot(history["train_loss"], label="Train Loss")
    plt.plot(history["val_loss"], label="Val Loss")
    plt.xlabel("Epoch")
    plt.ylabel("Loss")
    plt.legend()
    plt.title("Loss Curves")

    plt.subplot(1, 2, 2)
    plt.plot(history["train_acc"], label="Train Acc")
    plt.plot(history["val_acc"], label="Val Acc")
    plt.xlabel("Epoch")
    plt.ylabel("Accuracy")
    plt.legend()
    plt.title("Accuracy Curves")

    plt.tight_layout()
    curves_path = outputs_dir / "training_curves.png"
    plt.savefig(str(curves_path))
    plt.close()
    print(f"Saved training curves to {curves_path}")

if __name__ == "__main__":
    main()
