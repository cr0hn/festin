#!/bin/bash

if [ $# -ne 3 ]
  then
    echo "Usage: $0 inputFile outputDirectory dnsServer"
    echo 
    echo "inputFile: A txt file with one domain per file"
    echo "outputDirectory: directory to leave results"
    echo "dnsServer: the dns to use when resolving CNAMEs"
    echo
    echo "This command will iterate over all domains, and do the following for each"
    echo " - Run dnsrecon for crt.sh search to find related domains"
    echo " - Translate dnsrecon output for festin"
    echo " - Run festin with some specific option. Please, edit this script to fit your own preferences"
    exit 1
fi

mkdir $2

for i in $(cat $1); do 
    echo "Proccesing $i"
    if [ -f "$2/$i.csv" ]; then
        echo "  - Skiping generation of existing file $i.csv"
    else 
        dnsrecon -d $i -t crt -c $2/$i.csv
    fi
    if [ -f "$2/$i.domains" ]; then
        echo "  - Skiping generation of existing file $i.domains"
    else
        cat $2/$i.csv | tail -n +2 | sort -u | cut -d "," -f 2 >> $2/$i.domains
    fi
    if [ -f "$2/$i.result" ]; then
        echo "  - Skiping generation of existing file $i.result"
    else
        echo "  - Running festin with input file $i.domains"
        festin -f $2/$i.domains -c 5 -rr $2/$i.result.json --tor -ds $3 >$2/$i.stdout 2>$2/$i.stderr
        touch $2/$i.result
    fi
done