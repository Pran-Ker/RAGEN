# 

3.1 Experiments
```bash
# 10 experiments for bandits
bash scripts/analysis/bandits.sh

# 4 experiments for sokoban and 
bash scripts/analysis/multiturn.sh

# 16 experiments for ablation studies
bash scripts/analysis/ablation.sh



















bash train.sh sokoban \
    model.experiment_name=sokoban_main \
    system.cuda_visible_devices=0 \
    training.micro_batch_size=2 \
    training.total_training_steps=100 \
    trainer.test_freq=25  >> ./log/terminal/sokoban_main.log





```