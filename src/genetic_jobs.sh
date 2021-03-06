#!/bin/bash
set -x

learn='python genetic_programming_cca.py'

for  run in 0
do

for dataset in 'ohsumed' 'reuters21578' '20newsgroups'
do
    for cat in {0..115}
    do
        $learn "$dataset" "$cat" --run $run --outdir ../vectors_gen
        #pass
    done

    python classification_benchmark.py -v ../vectors_gen -r ../results/genetic.csv 
    
done

done
