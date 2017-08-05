import os
homeDir = os.path.expanduser('~')
import sys
sys.path.append(homeDir+'/miniconda2/lib/python2.7/site-packages')
from readfq import readfq
import argparse
import os
import re
import shlex
import subprocess
import numpy as np
from sklearn import preprocessing
import param

cigarRe = r"(\d+)([MIDNSHP=X])"
base2num = dict(zip("ACGT", (0,1,2,3)))

def GenerateTensor(ctgName, alns, center, refSeq):
    alnCode = np.zeros( (2*param.flankingBaseNum+1, 4, param.matrixNum) )
    for aln in alns:
        for refPos, queryAdv, refBase, queryBase in aln:
            if str(refBase) not in "ACGT-":
                continue
            if str(queryBase) not in "ACGT-":
                continue
            if refPos - center >= -(param.flankingBaseNum+1) and refPos - center < param.flankingBaseNum:
                offset = refPos - center + (param.flankingBaseNum+1)
                if queryBase != "-":
                    if refBase != "-":
                        alnCode[offset][ base2num[refBase] ][0] += 1.0
                        alnCode[offset][ base2num[queryBase] ][1] += 1.0
                        alnCode[offset][ base2num[refBase] ][2] += 1.0
                        alnCode[offset][ base2num[queryBase] ][3] += 1.0
                        #for i in [i for i in range(param.matrixNum) if i != base2num[refBase]]:
                        #    alnCode[offset][i][0] -= 0.333333
                        #    alnCode[offset][i][2] -= 0.333333
                        #for i in [i for i in range(param.matrixNum) if i != base2num[queryBase]]:
                        #    alnCode[offset][i][1] -= 0.333333
                        #    alnCode[offset][i][3] -= 0.333333
                    elif refBase == "-":
                        queryAdv = max(2*param.flankingBaseNum, offset+queryAdv) - offset
                        alnCode[offset+queryAdv][ base2num[queryBase] ][1] += 1.0
                        #for i in [i for i in range(param.matrixNum) if i != base2num[queryBase]]:
                        #    alnCode[offset+queryAdv][i][1] -= 0.333333
                    else:
                      print >> sys.stderr, "Should not reach here: %s, %s" % (refBase, queryBase)
                elif queryBase == "-":
                    if refBase != "-":
                        alnCode[offset][ base2num[refBase] ][2] += 1.0
                        #for i in [i for i in range(param.matrixNum) if i != base2num[refBase]]:
                        #    alnCode[offset][i][2] -= 0.333333
                    else:
                        print >> sys.stderr, "Should not reach here: %s, %s" % (refBase, queryBase)
                else:
                    print >> sys.stderr, "Should not reach here: %s, %s" % (refBase, queryBase)

    #for i in range(param.matrixNum):
    #    alnCode[:,:,i] = preprocessing.normalize(alnCode[:,:,i])

    outputLine = []
    outputLine.append( "%s %d %s" %  (ctgName, center, refSeq[center-(param.flankingBaseNum+1):center+param.flankingBaseNum]) )
    for x in np.reshape(alnCode, (2*param.flankingBaseNum+1)*4*param.matrixNum):
        outputLine.append("%0.3f" % x)
    return " ".join(outputLine)

def OutputAlnTensor(args):

    bam_fn = args.bam_fn
    can_fn = args.can_fn
    ctgName = args.ctgName
    ctgStart = args.ctgStart
    ctgEnd = args.ctgEnd
    samtools = args.samtools
    ref_fn = args.ref_fn
    tensor_fn = args.tensor_fn

    refSeq = None
    ref_fp = open(ref_fn, 'r')
    for name, seq, qual in readfq(ref_fp):
        if name != ctgName:
            continue
        refSeq = seq
        break

    if refSeq == None:
        print >> sys.stderr, "Cannot find reference sequence %s" % (ctgName)
        sys.exit(1)


    beginToEnd = {}
    with open(can_fn) as f:
        for row in f.readlines():
            row = row.strip().split()
            pos = int(row[1])
            beginToEnd[ pos-(param.flankingBaseNum+1) ] = (pos + (param.flankingBaseNum+1), pos)

    p = subprocess.Popen(shlex.split("%s view %s %s:%d-%d" % (samtools, bam_fn, ctgName, ctgStart, ctgEnd) ), stdout=subprocess.PIPE, bufsize=8388608)\
        if ctgStart and ctgEnd\
        else subprocess.Popen(shlex.split("%s view %s %s" % (samtools, bam_fn, ctgName) ), stdout=subprocess.PIPE, bufsize=8388608)

    centerToAln = {}

    tensor_fp = open(tensor_fn, "w")

    for l in p.stdout:
        l = l.strip().split()
        if l[0][0] == "@":
            continue

        QNAME = l[0]
        FLAG = int(l[1])
        RNAME = l[2]
        POS = int(l[3]) - 1 # switch from 1-base to 0-base to match sequence index
        CIGAR = l[5]
        SEQ = l[9]
        refPos = POS
        queryPos = 0

        endToCenter = {}
        activeSet = set()

        for m in re.finditer(cigarRe, CIGAR):
            advance = int(m.group(1))
            if m.group(2) == "S":
                queryPos += advance
            if m.group(2) in ("M", "=", "X"):
                matches = []
                for i in xrange(advance):
                    matches.append( (refPos, SEQ[queryPos]) )
                    if refPos in beginToEnd:
                        rEnd, rCenter = beginToEnd[refPos]
                        endToCenter[rEnd] = rCenter
                        activeSet.add(rCenter)
                        centerToAln.setdefault(rCenter, [])
                        centerToAln[rCenter].append([])
                    for center in list(activeSet):
                        centerToAln[center][-1].append( (refPos, 0, refSeq[refPos], SEQ[queryPos] ) )
                    if refPos in endToCenter:
                        center = endToCenter[refPos]
                        activeSet.remove(center)
                    refPos += 1
                    queryPos += 1

            elif m.group(2) == "I":
                queryAdv = 0
                for i in range(advance):
                    for center in list(activeSet):
                        centerToAln[center][-1].append( (refPos, queryAdv, "-", SEQ[queryPos] ))
                    queryPos += 1
                    queryAdv += 1

            elif m.group(2) == "D":
                for i in xrange(advance):
                    for center in list(activeSet):
                        centerToAln[center][-1].append( (refPos, 0, refSeq[refPos], "-" ))
                    if refPos in beginToEnd:
                        rEnd, rCenter = beginToEnd[refPos]
                        endToCenter[rEnd] = rCenter
                        activeSet.add(rCenter)
                        centerToAln.setdefault(rCenter, [])
                        centerToAln[rCenter].append([])
                    if refPos in endToCenter:
                        center = endToCenter[refPos]
                        activeSet.remove(center)
                    refPos += 1


        for center in centerToAln.keys():
            if center + (param.flankingBaseNum+1) < POS:
                l =  GenerateTensor(ctgName, centerToAln[center], center, refSeq)
                print >> tensor_fp, l
                del centerToAln[center]

    for center in centerToAln.keys():
        if center + (param.flankingBaseNum+1) < POS:
            l =  GenerateTensor(ctgName, centerToAln[center], center, refSeq)
            print >> tensor_fp, l


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
            description="Generate tensors summarizing local alignments from a BAM file and a list of candidate locations" )

    parser.add_argument('--bam_fn', type=str, default="input.bam",
            help="Sorted bam file input, default: %(default)s")

    parser.add_argument('--ref_fn', type=str, default="ref.fa",
            help="Reference fasta file input, default: %(default)s")

    parser.add_argument('--can_fn', type=str, default="pileup.out",
            help="Variant candidate list generated by ExtractVariantCandidates.py or true variant list generated by GetTruth.py, default: %(default)s")

    parser.add_argument('--tensor_fn', type=str, default="tensor.out",
            help="Tensor output, default: %(default)s")

    parser.add_argument('--ctgName', type=str, default="chr17",
            help="The name of sequence to be processed, default: %(default)s")

    parser.add_argument('--ctgStart', type=int, default=None,
            help="The 1-bsae starting position of the sequence to be processed")

    parser.add_argument('--ctgEnd', type=int, default=None,
            help="The inclusive ending position of the sequence to be processed")

    parser.add_argument('--samtools', type=str, default="samtools",
            help="Path to the 'samtools', default: %(default)s")

    args = parser.parse_args()

    OutputAlnTensor(args)

